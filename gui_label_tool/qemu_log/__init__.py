"""qemu_log -- consume QEMU's input-event trace and produce a clean event stream.

This bounded context tails the QEMU ``input_event`` trace log, decodes and
merges it into semantic user-input ``Event`` objects, and forwards them to the
annotation service.

- :mod:`keymap`   QEMU-specific qcode tables (pure data);
- :mod:`parser`   the ``Event`` data model plus the two pure passes that build
  it: ``QemuEventParser`` decodes a trace line, ``EventMerger`` merges the stream;
- :mod:`pipeline` tail loop wiring the parser + merger + a sink;
- :mod:`service`  FastAPI service running the pipeline and forwarding events.
"""
