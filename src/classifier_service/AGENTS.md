# AGENTS.md - Classifier Service

This directory contains the query classification service for intent detection.

## Structure

classifier_service/
|-- __init__.py              # Classifier exports
|-- classifier.py            # Main classifier implementation
|-- models.py                # Classification models
-- training.py              # Classifier training utilities

## Purpose
- Classifies search queries into intent categories
- Used by search pipeline for profile selection
- Supports both rule-based and ML-based classification

## Testing
pytest tests/test_classifier*.py -v (if exists)
