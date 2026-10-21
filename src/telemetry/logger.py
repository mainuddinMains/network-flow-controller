"""CSV and JSON experiment result logger."""

import csv
import json
from pathlib import Path


class ExperimentLogger:
    """Persist a MetricsRecorder's time series and summary as CSV and JSON.

    :param recorder: A ``MetricsRecorder`` whose history and summary are saved.
    :param metadata: Optional dict of experiment parameters to embed in JSON.
    """

    def __init__(self, recorder: object, metadata: dict | None = None) -> None:
        """Store the recorder and optional metadata."""
        self._recorder = recorder
        self._metadata = dict(metadata) if metadata else {}

    def write_csv(self, path: object) -> Path:
        """Write every recorded row to a CSV file and return the path.

        :param path: Destination file path (parent directories are created).
        :returns: Resolved ``Path`` of the written file.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = self._recorder.history
        if not rows:
            path.write_text("")
            return path
        fieldnames = list(rows[0].keys())
        with path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def write_json(self, path: object) -> Path:
        """Write summary and metadata to a JSON file and return the path.

        :param path: Destination file path (parent directories are created).
        :returns: Resolved ``Path`` of the written file.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw_summary = self._recorder.summary()
        # JSON keys must be strings; flow_id ints become string keys.
        flows_str = {
            str(fid): data for fid, data in raw_summary["flows"].items()
        }
        payload = {
            "metadata": self._metadata,
            "flows": flows_str,
            "summary": {
                "duration": raw_summary["duration"],
                "fairness": raw_summary["fairness"],
                "total_throughput_bytes_per_second": raw_summary[
                    "total_throughput_bytes_per_second"
                ],
                "flows": flows_str,
            },
        }
        path.write_text(json.dumps(payload, indent=2))
        return path
