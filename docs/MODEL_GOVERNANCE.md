# Model defaults and operational checks

The automatic model Council, qualification cycle and cross-service model promotion are retired. RAMS uses configured models and its existing bounded fallback routes. Authentication, provider availability, failed-request handling, usage/cost reporting and explicit operator controls remain active.

A small read-only startup loader retains the last saved model assignments so retiring automation does not silently change production models. The legacy state object is configuration only: no automatic selector, apply endpoint, monthly renewal or qualification gate can write new assignments. Historical records remain intact. New model choices are an explicit operator configuration change.

Repository code-review, engineering and content-quality councils are separate and remain active.
