from typing import List, TypedDict, Union
import orthanc

from .broker import BrokerConfig

class EventsConfig(TypedDict):
    StableSeries: bool
    StableStudy: bool
    StablePatient: bool
    DeletedSeries: bool
    DeletedStudy: bool
    DeletedPatient: bool
    DeletedInstance: bool
    StoredInstance: bool
    StoredInstanceSkipOrigin: List[str]
    StoredInstancePublishTags: List[str]
    StableSeriesPublishTags: List[str]
    StableStudyPublishTags: List[str]
    StablePatientPublishTags: List[str]


class DicomEventsConfig(TypedDict):
    Events: EventsConfig
    Broker: BrokerConfig

def map_origin_to_int(origin_value: str) -> int:
    # String to integer mapping
    origin_mapping = {
        "UNKNOWN": orthanc.InstanceOrigin.UNKNOWN,
        "DICOM_PROTOCOL": orthanc.InstanceOrigin.DICOM_PROTOCOL,
        "REST_API": orthanc.InstanceOrigin.REST_API,
        "PLUGIN": orthanc.InstanceOrigin.PLUGIN,
        "LUA": orthanc.InstanceOrigin.LUA,
        "WEB_DAV": orthanc.InstanceOrigin.WEB_DAV,
    }
    
    # Convert to uppercase for case-insensitive matching
    origin_str = str(origin_value).upper()
    
    if origin_str in origin_mapping:
        return origin_mapping[origin_str]
    else:
        orthanc.LogWarning(f"Unknown origin value: {origin_value}, using UNKNOWN")
        return orthanc.InstanceOrigin.UNKNOWN

def origin_to_string(origin_value: int) -> str:
    # Integer to string mapping
    origin_mapping = {
        orthanc.InstanceOrigin.UNKNOWN: "UNKNOWN",
        orthanc.InstanceOrigin.DICOM_PROTOCOL: "DICOM_PROTOCOL",
        orthanc.InstanceOrigin.REST_API: "REST_API",
        orthanc.InstanceOrigin.PLUGIN: "PLUGIN",
        orthanc.InstanceOrigin.LUA: "LUA",
        orthanc.InstanceOrigin.WEB_DAV: "WEB_DAV",
    }
    
    return origin_mapping.get(origin_value, "UNKNOWN")

def should_skip_origin(current_origin: int, skip_origins: List[str]) -> bool:
    for skip_origin in skip_origins:
        mapped_origin = map_origin_to_int(skip_origin)
        if current_origin == mapped_origin:
            return True
    return False