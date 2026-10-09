"""Prompt registry: Git-canonical prompt templates with an optional Langfuse mirror.

Cross-cutting package (feature 009). Agents and eval code call
`prompts.registry.resolve()`; nothing outside `prompts/langfuse_adapter.py`
may import `langfuse` (Principle II — see docs/adr/052 and ADR-053).
"""
