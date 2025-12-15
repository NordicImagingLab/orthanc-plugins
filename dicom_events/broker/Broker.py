from typing import Any, Dict, TypedDict, Literal
from .RabbitMQBroker import RabbitMQConfig
from .SNSBroker import SNSConfig
import logging

logger = logging.getLogger("orthanc_broker_events")


class BrokerConfig(TypedDict):
    Type: Literal["rabbitmq", "sqs-sns"]
    RabbitConfig: RabbitMQConfig
    SNSConfig: SNSConfig


class Broker:
    def __init__(self, config: BrokerConfig):
        if not isinstance(config, dict):
            raise TypeError("Config must be a dictionary")

        if "Type" not in config:
            raise KeyError("Config must contain 'Type' key")

        self.broker = None
        broker_type = config["Type"]

        if broker_type == "rabbitmq":
            # Import only when needed
            from .RabbitMQBroker import RabbitMQBroker

            self.broker = RabbitMQBroker(config["RabbitConfig"])
        elif broker_type == "sqs-sns":
            # Import only when needed
            from .SNSBroker import SNSBroker

            self.broker = SNSBroker(config["SNSConfig"])
        else:
            logger.error(f"Unsupported broker type: {broker_type}")
            raise ValueError(f"Unsupported broker type: {broker_type}")

    def connect(self) -> None:
        """Establish connection to the broker"""
        if self.broker is None:
            raise ValueError("Broker is not connected")

        try:
            self.broker.connect()
            logger.info("Connected to broker")
        except Exception as e:
            logger.error(f"Failed to connect to broker: {e}")

    def disconnect(self) -> None:
        """Close connection to the broker"""
        if self.broker is None:
            return

        try:
            self.broker.disconnect()
        except Exception as e:
            logger.error(f"Failed to disconnect from broker: {e}")
        self.broker = None

    def publish(self, payload: Dict[str, Any], target: str) -> None:
        """Publish a message to the broker"""
        if self.broker is None:
            logger.error("Can't publish message, broker is not connected")
            return

        try:
            self.broker.publish(payload, target)
        except Exception as e:
            logger.error(f"Failed to publish message: {e}")
