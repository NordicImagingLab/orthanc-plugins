from dataclasses import dataclass
import json
import threading
import orthanc

from series_private_tags import SeriesMainPrivateDicomTagsHandler
from dicom_events import DicomEvents
from series_thumbnail import SeriesThumbnail
import queue

# Create a thread-safe queue for processing instances
instance_queue = queue.Queue()


@dataclass
class InstanceData:
    """Data structure for queued instance processing"""

    id: str
    simple_tags: dict
    remote_ae: str | None
    remote_ip: str | None
    origin: orthanc.InstanceOrigin
    # You can add other fields as needed


def initialize_plugins():
    config = json.loads(orthanc.GetConfiguration())
    dicom_events_config = config.get("DicomEvents", {})
    series_private_tags_config = config.get("SeriesMainPrivateDicomTags", [])

    main_private_tags_plugin = SeriesMainPrivateDicomTagsHandler(
        series_private_tags_config
    )
    dicom_events_plugin = DicomEvents(dicom_events_config)
    series_thumbnail_plugin = SeriesThumbnail()

    def process_instances():
        while True:
            try:
                instance_data = instance_queue.get(timeout=1)
                instance_db_object = json.loads(
                    orthanc.RestApiGet(f"/instances/{instance_data.id}")
                )

                try:
                    main_private_tags_plugin.on_stored_instance(
                        instance_data.simple_tags, instance_db_object
                    )
                except Exception as e:
                    print(f"Error processing main private tags: {e}")

                try:
                    dicom_events_plugin.on_stored_instance(
                        instance_data.simple_tags,
                        instance_db_object,
                        instance_data.remote_ae,
                        instance_data.remote_ip,
                        instance_data.origin,
                    )
                except Exception as e:
                    print(f"Error processing DICOM events: {e}")
            except queue.Empty:
                # Just a timeout, keep waiting
                pass
            except Exception as e:
                print(f"Error processing instance: {e}")

    # Start background thread
    processor = threading.Thread(target=process_instances, daemon=True)

    def on_stored_instance(instance: orthanc.DicomInstance, instance_id: str):
        """
        Callback function for when an instance is stored

        Orthanc warns that this function runs synchronously in the orthanc core. Deadlocks can happen if
        calls to other orthanc primitives are done during this callback. Therefore, we queue the instance
        data for later processing in a separate thread.
        """

        remote_ip = None
        if instance.HasInstanceMetadata("RemoteIP"):
            remote_ip = instance.GetInstanceMetadata("RemoteIP")
        
        simple_json = json.loads(instance.GetInstanceSimplifiedJson())

        instance_data = InstanceData(
            id=instance_id,
            # The instance object is not thread safe, so we copy what we need here
            simple_tags=simple_json,
            remote_ae=instance.GetInstanceRemoteAet(),
            remote_ip=remote_ip,
            origin=instance.GetInstanceOrigin(),
        )
        instance_queue.put(instance_data)

    def on_change(
        change_type: orthanc.ChangeType,
        resource_type: orthanc.ResourceType,
        resource_id: str,
    ):
        series_thumbnail_plugin.on_change(change_type, resource_type, resource_id)
        dicom_events_plugin.on_change(change_type, resource_type, resource_id)

        if change_type == orthanc.ChangeType.ORTHANC_STARTED:
            processor.start()
        elif change_type == orthanc.ChangeType.ORTHANC_STOPPED:
            processor.join()

    orthanc.RegisterOnStoredInstanceCallback(on_stored_instance)
    orthanc.RegisterOnChangeCallback(on_change)


initialize_plugins()
