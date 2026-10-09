"""Local-environment compatibility shim — NOT part of the retrieval design.

This dev machine's Windows Application Control policy blocks loading
``scipy/interpolate/_rbfinterp_pythran*.pyd`` (a compiled RBF-interpolation
extension). ``sentence-transformers`` -> ``sklearn`` -> ``scipy.stats`` ->
``scipy.interpolate`` pulls that module in transitively at import time even
though nothing in this codebase performs RBF interpolation, so the whole
``sentence_transformers`` import fails before ``SentenceTransformer`` or
``CrossEncoder`` can even be constructed.

This module registers an inert stand-in for that one submodule *only* when
the real import fails, so environments where the policy doesn't apply (CI,
other developer machines, prod containers) take the real scipy module
unchanged — this is a fallback, not a replacement. Import this module
before importing anything from ``sentence_transformers``.
"""

from __future__ import annotations

import sys
import types


def _patch_blocked_rbf_extension() -> None:
    try:
        import scipy.interpolate

        return  # real import succeeded — nothing to patch
    except ImportError:
        pass

    def _unavailable(*_args: object, **_kwargs: object) -> None:
        raise NotImplementedError(
            "scipy.interpolate._rbfinterp_pythran is unavailable in this "
            "environment (blocked by local Application Control policy) and "
            "ResortFlow's RAG pipeline does not use RBF interpolation."
        )

    stub = types.ModuleType("scipy.interpolate._rbfinterp_pythran")
    for name in ("_build_system", "_build_evaluation_coefficients", "_polynomial_matrix"):
        setattr(stub, name, _unavailable)
    sys.modules["scipy.interpolate._rbfinterp_pythran"] = stub

    import scipy.interpolate  # noqa: F401  — retry now that the blocked submodule is stubbed


_patch_blocked_rbf_extension()
