"""Data pipeline: raw -> warehouse.

Unlike `data/`, this ships. The ETL runs in the deployed environment at
M6, so `pipeline` is listed in the deployment manifest (ADR-0001).
"""
