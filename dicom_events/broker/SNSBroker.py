import json
import threading
import time
import traceback
from typing import Dict, Any, TypedDict, Optional, List
from dataclasses import dataclass

import orthanc


class SNSConfig(TypedDict):
    TopicArn: str

@dataclass
class SNSMessage:
    message: Dict[str, Any]
    routingKey: str = ""


class SNSBroker:
    def __init__(self, config: SNSConfig):
        self.config = config
        self.topic_arn = config.get("TopicArn", "")
        self.sns_client = None

        # Internal queue and thread
        self._msg_queue: List[SNSMessage] = []
        self._msg_queue_lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ----------------------------
    # Interface: connect
    # ----------------------------
    def connect(self) -> None:
        """Connect to SNS and start background worker."""
        import boto3
        from botocore.exceptions import ClientError

        try:
            orthanc.LogInfo(f"Connecting to SNS topic: {self.topic_arn}")
            self.sns_client = boto3.client("sns")
            # This will raise if the topic doesn't exist or is inaccessible
            # It doesn't really "connect" since SNS are simply HTTP requests
            self.sns_client.get_topic_attributes(TopicArn=self.topic_arn)
            orthanc.LogInfo(f"Connected to SNS topic: {self.topic_arn}")

            # Start worker thread
            if not self._running:
                self._running = True
                self._thread = threading.Thread(
                    target=self._worker_loop, daemon=True
                )
                self._thread.start()

        except ClientError as e:
            orthanc.LogInfo(f"SNS ClientError: {e}")
            orthanc.LogInfo(traceback.format_exc())
            raise
        except Exception as e:
            orthanc.LogInfo(f"Unexpected error connecting to SNS: {e}")
            orthanc.LogInfo(traceback.format_exc())
            raise

    # ----------------------------
    # Interface: disconnect
    # ----------------------------
    def disconnect(self) -> None:
        """Stop background worker and disconnect SNS client."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.sns_client = None
        orthanc.LogInfo("SNSBroker disconnected")

    # ----------------------------
    # Interface: publish
    # ----------------------------
    def publish(self, message: Dict[str, Any], routingKey: str = "") -> None:
        """Enqueue a message to be published asynchronously."""
        msg = SNSMessage(message=message, routingKey=routingKey)
        with self._msg_queue_lock:
            self._msg_queue.append(msg)

    # ----------------------------
    # Internal: worker loop
    # ----------------------------
    def _worker_loop(self) -> None:
        while self._running:
            msg = self._get_next_msg()
            if msg is None:
                time.sleep(0.01)
                continue

            try:
                self._publish_msg(msg)
            except Exception as e:
                orthanc.LogInfo(f"SNS worker error: {e}")
                orthanc.LogInfo(traceback.format_exc())
                self.sns_client = None
                time.sleep(1.0)

    # ----------------------------
    # Internal: get next message
    # ----------------------------
    def _get_next_msg(self) -> Optional[SNSMessage]:
        with self._msg_queue_lock:
            if not self._msg_queue:
                return None
            return self._msg_queue.pop(0)

    # ----------------------------
    # Internal: publish a single message
    # ----------------------------
    def _publish_msg(self, msg: SNSMessage) -> None:
        if not self.sns_client:
            return

        message_json = json.dumps(msg.message)
        routing_key = msg.routingKey

        attributes = {}
        if routing_key:
            attributes = {
                "EventType": {"DataType": "String", "StringValue": routing_key}
            }

        response = self.sns_client.publish(
            TopicArn=self.topic_arn,
            Message=message_json,
            MessageAttributes=attributes,
            MessageStructure="string"
        )

        orthanc.LogMessage(
            f"Published SNS message: {message_json}, MessageId: {response['MessageId']}",
            "DICOMEvents",
            "SNSBroker",
            84,
            orthanc.LogCategory.PLUGINS,  # type: ignore
            orthanc.LogLevel.TRACE,       # type: ignore
        )
