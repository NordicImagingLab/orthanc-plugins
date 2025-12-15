# Orthanc Python Plugins

A collection of Python plugins for [Orthanc](https://www.orthanc-server.com/) DICOM server that extend its functionality with event publishing, private tag handling, and thumbnail generation.

## Plugins Overview

| Plugin | Description |
|--------|-------------|
| **dicom_events** | Publishes DICOM events (store, stable, delete) to message brokers (RabbitMQ or AWS SNS) |
| **series_private_tags** | Stores and exposes private DICOM tags as series metadata |
| **series_thumbnail** | Provides a REST endpoint to retrieve series thumbnails |

## Installation

1. Place the plugin files in a directory accessible to Orthanc (e.g., `/usr/share/orthanc/python_plugins/`)
2. Configure the Python plugin in your `orthanc.json`:

```json
{
  "Python": {
    "Path": "/usr/share/orthanc/python_plugins/__init__.py",
    "Verbose": true,
    "AllowThreads": true
  }
}
```

## Plugin Configuration

### DICOM Events Plugin

Publishes DICOM events to a message broker when resources are stored, become stable, or are deleted.

#### Configuration

```json
{
  "DicomEvents": {
    "Events": {
      "StableSeries": true,
      "StableStudy": true,
      "StablePatient": true,
      "DeletedSeries": true,
      "DeletedStudy": true,
      "DeletedPatient": true,
      "DeletedInstance": false,
      "StoredInstance": true,
      "StoredInstanceThrottleMs": 1000,
      "StoredInstanceSkipOrigin": ["REST_API"],
      "StoredInstancePublishTags": [
        "PrivateCreator",
        "PatientName",
        "SeriesDescription"
      ],
      "StableSeriesPublishTags": [
        "SeriesInstanceUID",
        "StudyInstanceUID",
        "SOPClassUID",
        "ImageType"
      ],
      "StableStudyPublishTags": [
        "StudyInstanceUID"
      ]
    },
    "Broker": {
      "Type": "rabbitmq",
      "RabbitConfig": {
        "URL": "amqp://user:password@localhost:5672/",
        "Exchange": "dicom-events"
      },
      "SNSConfig": {
        "TopicArn": "arn:aws:sns:region:account-id:topic-name"
      }
    }
  }
}
```

#### Event Options

| Option | Type | Description |
|--------|------|-------------|
| `StableSeries` | bool | Publish event when a series becomes stable |
| `StableStudy` | bool | Publish event when a study becomes stable |
| `StablePatient` | bool | Publish event when a patient becomes stable |
| `DeletedSeries` | bool | Publish event when a series is deleted |
| `DeletedStudy` | bool | Publish event when a study is deleted |
| `DeletedPatient` | bool | Publish event when a patient is deleted |
| `DeletedInstance` | bool | Publish event when an instance is deleted |
| `StoredInstance` | bool | Publish event when an instance is stored |
| `StoredInstanceThrottleMs` | int | Throttle stored instance events (milliseconds) |
| `StoredInstanceSkipOrigin` | list | Skip events from these origins: `UNKNOWN`, `DICOM_PROTOCOL`, `REST_API`, `PLUGIN`, `LUA`, `WEB_DAV` |
| `StoredInstancePublishTags` | list | DICOM tags to include in stored instance events |
| `StableSeriesPublishTags` | list | DICOM tags to include in stable series events |
| `StableStudyPublishTags` | list | DICOM tags to include in stable study events |

#### Broker Configuration

**RabbitMQ:**
```json
{
  "Type": "rabbitmq",
  "RabbitConfig": {
    "URL": "amqp://user:password@host:5672/",
    "Exchange": "exchange-name"
  }
}
```

**AWS SNS:**
```json
{
  "Type": "sqs-sns",
  "SNSConfig": {
    "TopicArn": "arn:aws:sns:region:account-id:topic-name"
  }
}
```

---

### Series Private Tags Plugin

Orthanc does not natively support private tags in `MainDicomTags`. This plugin stores specified private tags as series metadata and exposes them via custom REST endpoints.

#### Configuration

First, define your private tags in the `Dictionary` section and list the tags to track:

```json
{
  "Dictionary": {
    "0055,1012": ["UT", "SourceSeriesInstanceUID", 1, 1, "MATTERHORN_Prvt_Tags"],
    "0055,1011": ["UT", "QueueID", 1, 1, "MATTERHORN_Prvt_Tags"],
    "0055,1000": ["UT", "OutputGraphs", 1, 1, "MATTERHORN_Prvt_Tags"]
  },
  "SeriesMainPrivateDicomTags": [
    "SourceSeriesInstanceUID",
    "QueueID",
    "!!OutputGraphs"
  ],
  "UserMetadata": {
    "SeriesPrivateTags": 2000
  }
}
```

#### Tag Prefixes

| Prefix | Behavior |
|--------|----------|
| (none) | Store the full tag value |
| `!!` | Store only existence flag (`"EXISTS"`) - useful for large tags |

#### REST Endpoints

The plugin adds the following endpoints that return series data enriched with `MainPrivateDicomTags`:

| Endpoint | Description |
|----------|-------------|
| `GET /with-private-tags/series` | List all series with private tags |
| `GET /with-private-tags/series/{id}` | Get single series with private tags |
| `GET /with-private-tags/patients/{id}/series` | Get all series for a patient |
| `GET /with-private-tags/studies/{id}/series` | Get all series for a study |

#### Example Response

```json
{
  "ID": "series-id",
  "MainDicomTags": {
    "SeriesInstanceUID": "1.2.3.4.5"
  },
  "MainPrivateDicomTags": {
    "SourceSeriesInstanceUID": "1.2.3.4.6",
    "QueueID": "queue-123",
    "OutputGraphs": "EXISTS"
  }
}
```

---

### Series Thumbnail Plugin

Provides a REST endpoint to retrieve a thumbnail image from the middle instance of a series.

#### Configuration

Add the metadata key for storing thumbnail instance IDs:

```json
{
  "UserMetadata": {
    "ThumbnailInstanceID": 2001
  }
}
```

#### REST Endpoint

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/series/{id}/thumbnail` | GET | Returns a preview image of the middle instance |

The plugin automatically determines the thumbnail instance when a series becomes stable and caches the instance ID in metadata for faster subsequent requests.

---

## Complete Configuration Example

```json
{
  "Python": {
    "Path": "/usr/share/orthanc/python_plugins/__init__.py",
    "Verbose": true,
    "AllowThreads": true
  },
  "Dictionary": {
    "0055,1012": ["UT", "SourceSeriesInstanceUID", 1, 1, "MATTERHORN_Prvt_Tags"],
    "0055,1011": ["UT", "QueueID", 1, 1, "MATTERHORN_Prvt_Tags"],
    "0055,1000": ["UT", "OutputGraphs", 1, 1, "MATTERHORN_Prvt_Tags"]
  },
  "SeriesMainPrivateDicomTags": [
    "SourceSeriesInstanceUID",
    "QueueID",
    "!!OutputGraphs"
  ],
  "UserMetadata": {
    "SeriesPrivateTags": 2000,
    "ThumbnailInstanceID": 2001
  },
  "DicomEvents": {
    "Events": {
      "StableSeries": true,
      "StableStudy": true,
      "StablePatient": true,
      "DeletedSeries": true,
      "DeletedStudy": true,
      "DeletedPatient": true,
      "DeletedInstance": false,
      "StoredInstance": true,
      "StoredInstanceThrottleMs": 1000,
      "StoredInstanceSkipOrigin": ["REST_API"],
      "StoredInstancePublishTags": ["PatientName", "SeriesDescription"],
      "StableSeriesPublishTags": ["SeriesInstanceUID", "StudyInstanceUID"],
      "StableStudyPublishTags": ["StudyInstanceUID"]
    },
    "Broker": {
      "Type": "rabbitmq",
      "RabbitConfig": {
        "URL": "amqp://user:password@localhost:5672/",
        "Exchange": "dicom-events"
      },
      "SNSConfig": {
        "TopicArn": "arn:aws:sns:region:account-id:topic-name"
      }
    }
  }
}
```

## Dependencies

See `dicom_events/requirements.txt` for Python package dependencies.

## License

See [LICENSE](LICENSE) for details.
