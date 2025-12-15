import json
import orthanc

METADATA_KEY = "SeriesPrivateTags"
ENDPOINT_PREFIX = "with-private-tags"


class SeriesMainPrivateDicomTagsHandler:
    """
    Orthanc does not support having private tags in the MainDicomTags of a series.
    This is unfortunate since these tags are stored in the database and can quickly be retrieved.
    This plugin stores the private tags given in the configuration as metadata for each series.
    The plugin adds endpoints which return the series with the private tags included in a 'MainPrivateDicomTags' object.
    """

    def __init__(self, tags_of_interest: list[str]):
        self._init_tags(tags_of_interest)

        orthanc.RegisterRestCallback(
            f"/{ENDPOINT_PREFIX}/series", self._on_get_all_series  # type: ignore
        )
        orthanc.RegisterRestCallback(
            f"/{ENDPOINT_PREFIX}/series/(.*)", self._on_get_series  # type: ignore
        )
        orthanc.RegisterRestCallback(
            f"/{ENDPOINT_PREFIX}/patients/(.*)/series", self._on_get_patient_series  # type: ignore
        )
        orthanc.RegisterRestCallback(
            f"/{ENDPOINT_PREFIX}/studies/(.*)/series", self._on_get_study_series  # type: ignore
        )

    def _init_tags(self, tags_of_interest: list[str]):
        # Initialize tag sets
        self.full_value_tags = set()
        self.existence_only_tags = set()

        # Process tags based on prefix
        for tag in tags_of_interest:
            if tag.startswith("!!"):  # Existence-only tag
                # Strip the prefix and add to existence_only set
                real_tag_name = tag[2:]
                self.existence_only_tags.add(real_tag_name)
            else:  # Full value tag
                self.full_value_tags.add(tag)

        # Combined set of all tags we need to look for (without prefixes)
        self.privateTags = self.full_value_tags.union(self.existence_only_tags)

    def on_stored_instance(self, simple_tags: dict, instance_db_object: dict):
        """
        Extract private tags from the instance and store them in the series metadata.

        This will be run in a background thread, so be careful with any shared state.
        """
        try:
            # Get the series ID from the instance
            series_id = instance_db_object["ParentSeries"]

            # Check if we've already processed this series
            try:
                orthanc.RestApiGet(f"/series/{series_id}/metadata/{METADATA_KEY}")
                # If we reach here, metadata exists, so we've already processed this series
                return
            except Exception:
                pass

            found_private_tags = {}
            for tag in self.privateTags:
                if tag in simple_tags:
                    if tag in self.existence_only_tags:
                        found_private_tags[tag] = "EXISTS"  # Just indicate existence
                    else:  # Full value tag
                        found_private_tags[tag] = simple_tags[tag]  # Store full value

            if found_private_tags:
                # Store all private tags as a single JSON metadata entry
                metadata_value = json.dumps(found_private_tags)
                orthanc.RestApiPut(
                    f"/series/{series_id}/metadata/{METADATA_KEY}",
                    metadata_value.encode(),
                )

        except Exception as e:
            orthanc.LogError(f"Error processing private tags: {str(e)}")

    def _enrich_with_private_tags(self, series_data, drop_instances: bool = True):
        """
        Add private tags to series MainDicomTags (works with single series or list).

        Retrieves private tags stored as metadata for each series and adds them
        to the MainDicomTags dictionary, enriching the API response.
        """

        def enrich_single_series(series):
            try:
                series_id = series["ID"]
                private_tags = json.loads(
                    orthanc.RestApiGet(f"/series/{series_id}/metadata/{METADATA_KEY}")
                )
                series["MainPrivateDicomTags"] = private_tags
            except Exception:
                pass

            if drop_instances:
                series.pop("Instances", None)
            return series

        if isinstance(series_data, list):
            return list(map(enrich_single_series, series_data))
        else:
            return enrich_single_series(series_data)

    def _on_get_all_series(self, output: orthanc.RestOutput, url, **request):
        if request["method"] != "GET":
            output.SendMethodNotAllowed("GET")
            return

        series_json = json.loads(orthanc.RestApiGet("/series?expand"))
        series_json = self._enrich_with_private_tags(series_json)
        output.AnswerBuffer(json.dumps(series_json).encode(), "application/json")

    def _on_get_series(self, output: orthanc.RestOutput, url, **request):
        if request["method"] != "GET":
            output.SendMethodNotAllowed("GET")
            return

        series_id = request["groups"][0]
        series_json = json.loads(orthanc.RestApiGet(f"/series/{series_id}?expand"))
        series_json = self._enrich_with_private_tags(series_json)
        output.AnswerBuffer(json.dumps(series_json).encode(), "application/json")

    def _on_get_patient_series(self, output: orthanc.RestOutput, url, **request):
        if request["method"] != "GET":
            output.SendMethodNotAllowed("GET")
            return

        patient_id = request["groups"][0]
        series_json = json.loads(orthanc.RestApiGet(f"/patients/{patient_id}/series"))
        series_json = self._enrich_with_private_tags(series_json)
        output.AnswerBuffer(json.dumps(series_json).encode(), "application/json")

    def _on_get_study_series(self, output: orthanc.RestOutput, url, **request):
        if request["method"] != "GET":
            output.SendMethodNotAllowed("GET")
            return

        study_id = request["groups"][0]
        series_json = json.loads(orthanc.RestApiGet(f"/studies/{study_id}/series"))
        series_json = self._enrich_with_private_tags(series_json)
        output.AnswerBuffer(json.dumps(series_json).encode(), "application/json")
