"""Named export preferences, separate from alpha presets and editor projects."""
import json
import logging

logger = logging.getLogger(__name__)


class VideoExportPresets:
    KEY = "video_export_presets"
    FIELDS = {"format", "fps", "keep_audio", "mute_audio", "volume"}

    def __init__(self, settings):
        self.settings = settings

    @classmethod
    def validate(cls, record):
        if not isinstance(record, dict) or set(record) != cls.FIELDS:
            raise ValueError("Invalid export preset fields.")
        if record["format"] not in ("gif", "mp4"):
            raise ValueError("Unsupported export format.")
        for key, low, high in (("fps", 1, 60), ("volume", 0, 200)):
            if type(record[key]) is not int or not low <= record[key] <= high:
                raise ValueError(f"Invalid export preset {key}.")
        for key in ("keep_audio", "mute_audio"):
            if type(record[key]) is not bool:
                raise ValueError(f"Invalid export preset {key}.")
        return dict(record)

    def _read(self):
        try:
            data = json.loads(self.settings.get(self.KEY, '{"version":1,"presets":{}}'))
        except (ValueError, TypeError) as exc:
            raise ValueError("Stored export presets are malformed; nothing was changed.") from exc
        if (not isinstance(data, dict) or type(data.get("version")) is not int
                or data["version"] != 1 or not isinstance(data.get("presets"), dict)):
            raise ValueError("Stored export presets are unsupported; nothing was changed.")
        return data

    def available(self):
        result = {}
        for name, record in self._read()["presets"].items():
            if not isinstance(name, str) or not name.strip() or len(name) > 80:
                continue
            try:
                result[name] = self.validate(record)
            except ValueError:
                logger.warning("Ignoring malformed video export preset %r", name, exc_info=True)
                continue
        return result

    def save(self, name, record):
        if not isinstance(name, str):
            raise ValueError("Enter a preset name of 1–80 characters.")
        name = name.strip()
        if not name or len(name) > 80:
            raise ValueError("Enter a preset name of 1–80 characters.")
        record = self.validate(record)
        data = self._read()
        if name in data["presets"]:
            raise ValueError("That name already exists; choose a new name or delete it first.")
        data["presets"][name] = record
        self._write(data)
        return name

    def delete(self, name):
        data = self._read()
        if name not in self.available():
            raise ValueError("Select a valid export preset to delete.")
        del data["presets"][name]
        self._write(data)

    def _write(self, data):
        self.settings.set(self.KEY, json.dumps(data, allow_nan=False))
        self.settings.sync()
