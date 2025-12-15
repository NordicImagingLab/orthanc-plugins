import json
import orthanc


class SeriesThumbnail:
    def __init__(self):
        orthanc.RegisterRestCallback(
            "/series/(.*)/thumbnail", self._handle_thumbnail_request  # type: ignore
        )

    def on_change(
        self,
        change_type: orthanc.ChangeType,
        resource_type: orthanc.ResourceType,
        resource_id: str,
    ):
        if change_type == orthanc.ChangeType.STABLE_SERIES:
            self._get_instance_thumbnail_id(resource_id, True)

    def _get_instance_thumbnail_id(
        self, series_id: str, force_update: bool
    ) -> str | None:
        if not force_update:
            try:
                thumbnail_instance_id = orthanc.RestApiGet(
                    f"/series/{series_id}/metadata/ThumbnailInstanceID"
                ).decode()
                if thumbnail_instance_id:
                    return thumbnail_instance_id
            except Exception:
                pass

        try:
            series = json.loads(orthanc.RestApiGet(f"/series/{series_id}"))
            if not series:
                print(f"SeriesThumbnail: Series {series_id} not found")
                return None
        except Exception as e:
            print(f"SeriesThumbnail: Error fetching series {series_id}: {str(e)}")
            return None

        number_of_instances = len(series["Instances"])
        if number_of_instances == 0:
            print(f"SeriesThumbnail: No instances in series {series_id}")
            return
        target_thumbnail_instance_number = number_of_instances // 2

        # Fallback
        thumbnail_instance_id = series["Instances"][target_thumbnail_instance_number]

        try:
            instances = json.loads(orthanc.RestApiGet(f"/series/{series_id}/instances"))
            for instance in instances:
                instance_number = instance.get("MainDicomTags", {}).get(
                    "InstanceNumber"
                )
                if instance_number == str(target_thumbnail_instance_number):
                    thumbnail_instance_id = instance["ID"]
                    break
        except Exception as e:
            print(
                f"SeriesThumbnail: Error fetching instances for series {series_id}: {str(e)}"
            )
            return thumbnail_instance_id

        try:
            orthanc.RestApiPut(
                f"/series/{series_id}/metadata/ThumbnailInstanceID",
                thumbnail_instance_id.encode(),
            )
        except Exception as e:
            print(
                f"SeriesThumbnail: Error setting thumbnail instance ID for series {series_id}: {str(e)}"
            )

        return thumbnail_instance_id

    def _handle_thumbnail_request(self, output: orthanc.RestOutput, url, **request):
        if request["method"] != "GET":
            output.SendMethodNotAllowed("GET")
            return

        series_id = request["groups"][0]
        if not series_id:
            output.SetHttpErrorDetails("Series ID is required", 0)
            output.SendHttpStatusCode(400)
            return

        thumbnail_instance_id = self._get_instance_thumbnail_id(series_id, False)
        if thumbnail_instance_id is None:
            output.SetHttpErrorDetails("No thumbnail instance found", 0)
            output.SendHttpStatusCode(404)
            return

        try:
            res = orthanc.RestApiGet(
                f"/instances/{thumbnail_instance_id}/frames/0/preview"
            )
        except Exception as e:
            print(
                f"SeriesThumbnail: Error fetching thumbnail for instance {thumbnail_instance_id}: {str(e)}"
            )
            output.SetHttpErrorDetails("Error fetching thumbnail", 0)
            output.SendHttpStatusCode(500)
            return

        output.AnswerBuffer(res, "image/png")
