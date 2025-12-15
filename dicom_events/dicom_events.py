from dataclasses import dataclass, field
import json
import threading
import time
from typing import Any, Dict, Optional
import orthanc
from enum import Enum
from .broker import Broker
from .config import EventsConfig, DicomEventsConfig, should_skip_origin, origin_to_string


class DicomEventID(Enum):
    STABLE_SERIES = "dicom.series.stable"
    STABLE_STUDY = "dicom.study.stable"
    STABLE_PATIENT = "dicom.patient.stable"
    STORED_INSTANCE = "dicom.instance.stored"
    PATIENT_DELETED = "dicom.patient.deleted"
    STUDY_DELETED = "dicom.study.deleted"
    SERIES_DELETED = "dicom.series.deleted"
    INSTANCE_DELETED = "dicom.instance.deleted"
    STORED_PATIENTS = "dicom.report.stored-patients"


@dataclass
class BaseEventPayload:
    identity: str
    tags: Dict[str, Any] = field(default_factory=dict)

    def toDict(self) -> Dict[str, Any]:
        """
        Convert the payload to a dictionary for serialization.
        Includes additionalProps flattened into the main dictionary.
        """
        result = {}

        # Add all dataclass fields except additionalProps
        for key, value in self.__dict__.items():
            if key != "tags" and value is not None:
                result[key] = value

        # Add additionalProps flattened
        if self.tags:
            result.update(self.tags)

        return result


@dataclass
class StablePatientPayload(BaseEventPayload):
    patientID: str = ""

    def __post_init__(self):
        if not self.identity:
            self.identity = DicomEventID.STABLE_PATIENT.value


@dataclass
class StableStudyPayload(BaseEventPayload):
    studyID: str = ""
    patientID: str = ""

    def __post_init__(self):
        if not self.identity:
            self.identity = DicomEventID.STABLE_STUDY.value


@dataclass
class StableSeriesPayload(BaseEventPayload):
    seriesID: str = ""
    studyID: str = ""
    patientID: str = ""

    def __post_init__(self):
        if not self.identity:
            self.identity = DicomEventID.STABLE_SERIES.value


@dataclass
class StoredInstancePayload(BaseEventPayload):
    seriesID: str = ""
    instanceID: str = ""
    instanceCount: int = 0
    remoteAET: Optional[str] = None
    remoteIP: Optional[str] = None
    origin: str = ""

    def __post_init__(self):
        if not self.identity:
            self.identity = DicomEventID.STORED_INSTANCE.value


@dataclass
class DeletedResourcePayload(BaseEventPayload):
    type: str = ""
    ID: str = ""

@dataclass
class PublishStoredPatientResourcePayload(BaseEventPayload):
    patientIDs: Dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self):
        if not self.identity:
            self.identity = DicomEventID.STORED_PATIENTS.value

@dataclass
class SeriesStoreState:
    count: int = 0
    last_publish_ms: float = 0.0
    trailing_timer: Optional[threading.Timer] = None
    trailing_payload: Optional[StoredInstancePayload] = None


class DicomEvents:
    def __init__(self, config: DicomEventsConfig):
        self.broker: Broker = Broker(config["Broker"])
        self.config: EventsConfig = config["Events"]
        self.incoming_series_tag_filter = []
        self.series_state: dict[str, SeriesStoreState] = {}
        self.series_state_lock = threading.Lock()
        self.stored_instance_throttle_ms: int = self.config.get("StoredInstanceThrottleMs", 0)

    def on_change(
        self,
        change_type: orthanc.ChangeType,
        resource_type: orthanc.ResourceType,
        resource_id: str,
    ):
        match change_type:
            case orthanc.ChangeType.STABLE_SERIES:
                self._on_stable_series(resource_id)
            case orthanc.ChangeType.STABLE_STUDY:
                self._on_stable_study(resource_id)
            case orthanc.ChangeType.STABLE_PATIENT:
                self.on_stable_patient(resource_id)
            case orthanc.ChangeType.DELETED:
                self._on_resource_deleted(resource_type, resource_id)
            case orthanc.ChangeType.ORTHANC_STARTED:
                self.broker.connect()
                self.on_orthanc_started()
            case orthanc.ChangeType.ORTHANC_STOPPED:
                self.broker.disconnect()

    def on_orthanc_started(self):
        payload = PublishStoredPatientResourcePayload(
            identity=DicomEventID.STORED_PATIENTS.value
        )

        try:
            payload.patientIDs = json.loads(orthanc.RestApiGet(f"/patients/"))
        except Exception as e:
            print(f"DicomEvents: Error getting patient to populate message: {e}")

        self.broker.publish(payload.toDict(), payload.identity)

    def _on_resource_deleted(
        self, resource_type: orthanc.ResourceType, resource_id: str
    ):
        type = "unknown"
        target = ""

        match resource_type:
            case orthanc.ResourceType.PATIENT:
                if not self.config["DeletedPatient"]:
                    return
                type = "patient"
                target = DicomEventID.PATIENT_DELETED.value
            case orthanc.ResourceType.STUDY:
                if not self.config["DeletedStudy"]:
                    return
                type = "study"
                target = DicomEventID.STUDY_DELETED.value
            case orthanc.ResourceType.SERIES:
                if not self.config["DeletedSeries"]:
                    return
                type = "series"
                target = DicomEventID.SERIES_DELETED.value
                with self.series_state_lock:
                    if resource_id in self.series_state:
                        del self.series_state[resource_id]
            case orthanc.ResourceType.INSTANCE:
                if not self.config["DeletedInstance"]:
                    return
                type = "instance"
                target = DicomEventID.INSTANCE_DELETED.value

        
        payload = DeletedResourcePayload(
            identity=target,
            type=type,
            ID=resource_id,
        )

        self.broker.publish(payload.toDict(), payload.identity)

    def on_stable_patient(self, patient_id: str):
        if not self.config["StablePatient"]:
            return

        payload = StablePatientPayload(
            identity=DicomEventID.STABLE_PATIENT.value,
            patientID=patient_id,
        )

        try:
            patient = json.loads(orthanc.RestApiGet(f"/patients/{patient_id}"))
            stable_patient_tags = self.config.get("StablePatientPublishTags")
            if stable_patient_tags is not None:
                main_dicom_tags = patient.get("MainDicomTags", {})
                for tag in stable_patient_tags:
                    if tag in main_dicom_tags:
                        payload.tags[tag] = main_dicom_tags[tag]

        except Exception as e:
            print(f"DicomEvents: Error getting patient to populate message: {e}")

        self.broker.publish(payload.toDict(), payload.identity)

    def _on_stable_study(self, study_id: str):
        if not self.config["StableStudy"]:
            return

        payload = StableStudyPayload(
            identity=DicomEventID.STABLE_STUDY.value,
            studyID=study_id,
            patientID="unknown",
        )

        try:
            study = json.loads(orthanc.RestApiGet(f"/studies/{study_id}"))
            payload.patientID = study["ParentPatient"]

            stable_study_tags = self.config.get("StableStudyPublishTags")
            if stable_study_tags is not None:
                main_dicom_tags = study.get("MainDicomTags", {})
                for tag in stable_study_tags:
                    if tag in main_dicom_tags:
                        payload.tags[tag] = main_dicom_tags[tag]

        except Exception as e:
            print(f"DicomEvents: Error getting study to populate message: {e}")

        self.broker.publish(payload.toDict(), payload.identity)

    def _on_stable_series(self, series_id: str):
        if not self.config["StableSeries"]:
            return

        with self.series_state_lock:
            if series_id in self.series_state:
                del self.series_state[series_id]

        payload = StableSeriesPayload(
            identity=DicomEventID.STABLE_SERIES.value,
            seriesID=series_id,
            studyID="unknown",
            patientID="unknown",
        )

        try:
            series = json.loads(orthanc.RestApiGet(f"/series/{series_id}"))
            patient = json.loads(orthanc.RestApiGet(f"/series/{series_id}/patient"))
            payload.studyID = series["ParentStudy"]
            payload.patientID = patient["ID"]

            stable_series_tags = self.config.get("StableSeriesPublishTags")
            if stable_series_tags is not None:
                main_dicom_tags = series.get("MainDicomTags", {})
                for tag in stable_series_tags:
                    if tag in main_dicom_tags:
                        payload.tags[tag] = main_dicom_tags[tag]

        except Exception as e:
            print(f"DicomEvents: Error getting series to populate message: {e}")

        self.broker.publish(payload.toDict(), payload.identity)

    def on_stored_instance(
        self,
        simple_tags: dict,
        instance_db_object: dict,
        remote_aet: str | None,
        remote_ip: str | None,
        origin: int,
    ) -> None:
        if not self.config["StoredInstance"]:
            return

        if self.config["StoredInstanceSkipOrigin"] and should_skip_origin(origin, self.config["StoredInstanceSkipOrigin"]):
            return

        series_id = instance_db_object["ParentSeries"]
        throttle_ms = self.stored_instance_throttle_ms
        now = time.monotonic() * 1000

        # Prepare payload outside lock
        payload = StoredInstancePayload(
            identity=DicomEventID.STORED_INSTANCE.value,
            seriesID=series_id,
            instanceID=instance_db_object["ID"],
            remoteAET=remote_aet,
            remoteIP=remote_ip,
            origin=origin_to_string(origin),
        )
        if self.config["StoredInstancePublishTags"] is not None:
            for tag in self.config["StoredInstancePublishTags"]:
                if tag in simple_tags:
                    payload.tags[tag] = simple_tags[tag]

        with self.series_state_lock:
            state = self.series_state.get(series_id)
            if state is None:
                state = SeriesStoreState()
                self.series_state[series_id] = state

            state.count += 1
            payload.instanceCount = state.count

            # Throttling logic
            # Will delay publishing if within throttle period, but ensure last message is sent
            if throttle_ms > 0:
                if (now - state.last_publish_ms) < throttle_ms:
                    # Schedule trailing message
                    state.trailing_payload = payload
                    if state.trailing_timer is not None:
                        state.trailing_timer.cancel()
                    def send_trailing():
                        with self.series_state_lock:
                            if state.trailing_payload:
                                self.broker.publish(state.trailing_payload.toDict(), state.trailing_payload.identity)
                                state.last_publish_ms = time.monotonic() * 1000
                                state.trailing_payload = None
                                state.trailing_timer = None
                    state.trailing_timer = threading.Timer(throttle_ms / 1000.0, send_trailing)
                    state.trailing_timer.start()
                    return
                # Send immediately, cancel any trailing timer
                state.last_publish_ms = now
                if state.trailing_timer is not None:
                    state.trailing_timer.cancel()
                    state.trailing_timer = None
                    state.trailing_payload = None

        
        self.broker.publish(payload.toDict(), payload.identity)