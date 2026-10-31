"""<spec_root>/conftest.py: shared by every spec test (a maintenance path).

Registers the hypothesis profiles the gate and `tdd verify` select. The gate's profile is
reproducible and keeps no example database (which ignores -p no:cacheprovider).
"""

from hypothesis import settings

settings.register_profile("tdd-gate", derandomize=True, database=None, deadline=None, print_blob=True)
settings.register_profile("tdd-verify", derandomize=False, database=None, deadline=None,
                          max_examples=1000, print_blob=True)
