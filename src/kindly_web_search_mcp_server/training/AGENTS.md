# AGENTS.md - Training

This directory contains training data generation and model fine-tuning infrastructure.

## Structure

training/
|-- __init__.py              # Training exports
|-- data_gen.py              # Training data generation from search logs
|-- jsonl_exporter.py        # JSONL export for fine-tuning
-- models.py                # Training model configurations

## Purpose
- Generates training data from production search logs
- Exports JSONL for fine-tuning (SFT/DPO/RFT)
- Manages training job configurations

## Testing
pytest tests/test_training*.py -v (if exists)
