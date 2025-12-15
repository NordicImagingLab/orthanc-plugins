import json
import time
import threading
import queue
import random
import traceback
from typing import Dict, Any, TypedDict
from urllib.parse import urlparse

import orthanc

# ---- config typed dict
class RabbitMQConfig(TypedDict, total=False):
    URL: str
    Exchange: str
    Heartbeat: int          # seconds, default 30
    SleepInterval: float    # seconds, inner loop sleep (default 0.05)
    ConfirmPublish: bool    # enable publisher confirms (default False)
    MaxReconnectDelay: int  # max backoff in seconds (default 60)


class RabbitMQBroker:
    def __init__(self, config: RabbitMQConfig):
        if "URL" not in config or not config["URL"]:
            raise ValueError("RabbitMQ URL is required in configuration")

        self.rabbitmq_url = config["URL"]
        self.exchange = config.get("Exchange", "e.dicom")
        self.heartbeat = int(config.get("Heartbeat", 30))
        self.sleep_interval = float(config.get("SleepInterval", 0.05))
        self.confirm_publish = bool(config.get("ConfirmPublish", False))
        self.max_reconnect_delay = int(config.get("MaxReconnectDelay", 60))

        # Threading & queues
        self._publish_queue: "queue.Queue[tuple[Dict[str, Any], str]]" = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="RabbitMQPublisher")

        # Pika connection objects (created in thread)
        self.connection = None
        self.channel = None
        self._delivery_lock = threading.Lock()

        # Log initialization details
        try:
            parsed = urlparse(self.rabbitmq_url)
            host = parsed.hostname or "unknown"
            port = parsed.port or 5672
            vhost = (parsed.path[1:] if parsed.path.startswith("/") else parsed.path or "/")
        except Exception:
            host = "unknown"
            port = "unknown"
            vhost = "unknown"

        orthanc.LogInfo(
            f"Initializing RabbitMQBroker host={host}, port={port}, vhost={vhost}, exchange={self.exchange}"
        )

    # ----------------------------
    # Interface: connect
    # ----------------------------
    def connect(self) -> None:
        if not self._thread.is_alive():
            self._stop_event.clear()
            self._thread.start()
            orthanc.LogInfo("RabbitMQPublisher thread started")

    # ----------------------------
    # Interface: disconnect
    # ----------------------------
    def disconnect(self) -> None:
        orthanc.LogInfo("Stopping RabbitMQPublisher thread...")
        self._stop_event.set()
        # wait a short time for thread to exit gracefully
        self._thread.join(timeout=2.0)
        # ensure connection closed
        try:
            if self.connection and self.connection.is_open:
                self.connection.close()
                orthanc.LogInfo("RabbitMQ connection closed")
        except Exception as e:
            orthanc.LogError(f"Error closing RabbitMQ connection: {e}")
            orthanc.LogError(traceback.format_exc())

    # ----------------------------
    # Interface: publish
    # ----------------------------
    def publish(self, message: Dict[str, Any], routingKey: str = "") -> None:
        # keep it simple and non-blocking from callers (Orthanc callbacks)
        try:
            self._publish_queue.put_nowait((message, routingKey))
        except queue.Full:
            orthanc.LogError("RabbitMQ publish queue full; message dropped")


    def _ensure_connection(self, parameters):
        """
        Establishes a connection and channel if not already open.
        Must be called from the broker thread.
        """
        from pika import BlockingConnection
        from pika.exchange_type import ExchangeType

        if self.connection and self.connection.is_open:
            return

        # open connection
        orthanc.LogInfo("Attempting RabbitMQ connection...")
        self.connection = BlockingConnection(parameters)
        self.channel = self.connection.channel()

        # declare exchange
        self.channel.exchange_declare(
            exchange=self.exchange, exchange_type=ExchangeType.topic, durable=True
        )

        # Optional publisher confirms for reliability
        if self.confirm_publish:
            try:
                self.channel.confirm_delivery()
                orthanc.LogInfo("Publisher confirms enabled")
            except Exception:
                orthanc.LogInfo("Publisher confirm could not be enabled (continuing without confirms)")

        orthanc.LogInfo("Connected to RabbitMQ")

    def _run(self) -> None:
        """
        Main thread loop:
          - tries to connect (with backoff) and declare exchange
          - pumps pika I/O (process_data_events) every iteration to keep heartbeats
          - consumes messages from local queue and publishes them
          - reconnects on error with exponential backoff + jitter
        """
        from pika import URLParameters, BasicProperties

        reconnect_delay = 1.0

        parameters = URLParameters(self.rabbitmq_url)
        parameters.heartbeat = self.heartbeat

        while not self._stop_event.is_set():
            try:
                # If not connected, try to connect (may raise)
                if not self.connection or not self.connection.is_open:
                    try:
                        self._ensure_connection(parameters)
                        reconnect_delay = 1.0  # reset backoff after successful connect
                    except Exception as e:
                        orthanc.LogError(f"RabbitMQ connect failed: {e}")
                        orthanc.LogError(traceback.format_exc())
                        sleep_for = min(self.max_reconnect_delay, reconnect_delay) + random.random()
                        orthanc.LogInfo(f"Reconnect in {sleep_for:.1f}s")
                        # wait but respect stop event
                        self._stop_event.wait(sleep_for)
                        reconnect_delay = min(self.max_reconnect_delay, reconnect_delay * 2)
                        continue

                # Main publish loop while connected
                while not self._stop_event.is_set() and self.connection and self.connection.is_open:
                    try:
                        # If this isn't done, Rabbit will close the connection after heartbeat timeout
                        try:
                            self.connection.process_data_events(time_limit=0) # type: ignore
                        except Exception as e:
                            # Any exception here likely indicates connection trouble
                            orthanc.LogError(f"process_data_events error: {e}")
                            raise

                        try:
                            message, routingKey = self._publish_queue.get(timeout=0.1)
                        except queue.Empty:
                            # nothing to publish, sleep a bit to avoid CPU spin
                            self._stop_event.wait(self.sleep_interval)
                            continue

                        try:
                            body = json.dumps(message)
                        except Exception:
                            orthanc.LogError("Failed to serialize message to JSON; dropping message")
                            orthanc.LogError(traceback.format_exc())
                            continue

                        try:
                            # If publisher confirms are enabled, basic_publish will raise on failure to confirm
                            with self._delivery_lock:
                                self.channel.basic_publish( # type: ignore
                                    exchange=self.exchange,
                                    routing_key=routingKey,
                                    body=body,
                                    properties=BasicProperties(content_type="application/json"),
                                )
                        except Exception as e:
                            orthanc.LogError(f"Publish error — requeueing message: {e}")
                            orthanc.LogError(traceback.format_exc())
                            # If publish fails, push the message back into queue for retry
                            try:
                                self._publish_queue.put_nowait((message, routingKey))
                            except queue.Full:
                                orthanc.LogError("Publish queue full while requeueing failed message; message lost")
                            # break to outer reconnect logic so we re-evaluate connection state
                            raise

                        orthanc.LogMessage(
                            f"Published message routingKey={routingKey}",
                            "DICOMEvents",
                            "RabbitMQBroker",
                            208,
                            orthanc.LogCategory.PLUGINS,  # type: ignore
                            orthanc.LogLevel.TRACE,  # type: ignore
                        )

                    except Exception:
                        # Any exception inside publish loop should cause us to close/reconnect
                        try:
                            if self.connection and self.connection.is_open:
                                try:
                                    self.connection.close()
                                except Exception:
                                    pass
                        finally:
                            self.connection = None
                            self.channel = None

                        # reconnect with backoff
                        sleep_for = min(self.max_reconnect_delay, reconnect_delay) + random.random()
                        orthanc.LogInfo(f"Connection lost; reconnecting in {sleep_for:.1f}s")
                        self._stop_event.wait(sleep_for)
                        reconnect_delay = min(self.max_reconnect_delay, reconnect_delay * 2)
                        break

            except Exception as e:
                orthanc.LogError(f"Unexpected error in RabbitMQ thread: {e}")
                orthanc.LogError(traceback.format_exc())
                # Ensure we don't busy-loop on unexpected errors
                self._stop_event.wait(1.0)

        # thread is stopping: try to close cleanly
        try:
            if self.connection and self.connection.is_open:
                self.connection.close()
        except Exception:
            pass

        orthanc.LogInfo("RabbitMQPublisher thread exiting")
