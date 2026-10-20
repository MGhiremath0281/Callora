"""Module: Lead & Customer Import (Module 1).

Turns heterogeneous input (CSV uploads and direct JSON submissions) into
the canonical lead schema. Layout:

* ``schemas.py``    — request/response models, incl. the standardized
                      ``LeadRead`` output contract shared by all inputs;
* ``service.py``    — import pipeline orchestration (parse -> map ->
                      validate -> dedup -> persist -> report -> publish);
* ``validators.py`` — row-level validation into canonical lead values;
* ``normalizer.py`` — field-level normalization helpers;
* ``mapping.py``    — automatic CSV column-to-canonical-field mapping;
* ``dedup.py``      — duplicate detection (DB + in-file);
* ``events.py``     — transactional outbox for ``lead.created`` events.
"""
