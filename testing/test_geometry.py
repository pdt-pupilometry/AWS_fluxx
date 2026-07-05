"""
Valida la matemática de geometría (findContours -> fitEllipse -> área
analítica) SIN necesitar el modelo ONNX real, importando solo las funciones
puras de yolo_onnx.py (la sesión ONNX se carga perezosa, ver yolo_onnx._get_session).

Ejecutar:
    python -m pytest testing/test_geometry.py -v
"""

import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "functions" / "inference"))

from yolo_onnx import compute_pupil_iris_ratio, ellipse_area_from_mask  # noqa: E402


def _draw_ellipse_mask(size: int, center: tuple[int, int], axes: tuple[int, int]) -> np.ndarray:
    mask = np.zeros((size, size), dtype=np.uint8)
    cv2.ellipse(mask, center, axes, angle=0, startAngle=0, endAngle=360, color=1, thickness=-1)
    return mask


def test_ellipse_area_matches_analytical_formula():
    axes = (60, 40)  # semiejes (a, b)
    mask = _draw_ellipse_mask(size=256, center=(128, 128), axes=axes)
    expected_area = math.pi * axes[0] * axes[1]

    area = ellipse_area_from_mask(mask)

    assert area == pytest_approx(expected_area, rel_tol=0.03)


def test_none_mask_returns_zero():
    assert ellipse_area_from_mask(None) == 0.0


def test_empty_mask_returns_zero():
    mask = np.zeros((256, 256), dtype=np.uint8)
    assert ellipse_area_from_mask(mask) == 0.0


def test_degenerate_contour_returns_zero_not_exception():
    # Menos de 5 puntos: un cuadradito de 2x2 px genera un contorno muy chico;
    # fitEllipse debe fallar de forma controlada y devolver 0, nunca lanzar.
    mask = np.zeros((256, 256), dtype=np.uint8)
    mask[10:12, 10:12] = 1
    assert ellipse_area_from_mask(mask) == 0.0


def test_ratio_zero_when_iris_not_detected_no_zero_division():
    # Iris no detectado (área 0) pero pupila sí: sin el guard sería ZeroDivisionError.
    assert compute_pupil_iris_ratio(pupil_area=120.0, iris_area=0.0) == 0.0


def test_ratio_zero_when_pupil_not_detected():
    # Pupila no detectada, iris sí: 0 / iris_area, sin error, resultado 0.
    assert compute_pupil_iris_ratio(pupil_area=0.0, iris_area=300.0) == 0.0


def test_ratio_zero_when_neither_detected():
    assert compute_pupil_iris_ratio(pupil_area=0.0, iris_area=0.0) == 0.0


def test_ratio_normal_case():
    assert compute_pupil_iris_ratio(pupil_area=100.0, iris_area=400.0) == 0.25


def pytest_approx(expected: float, rel_tol: float) -> "_Approx":
    return _Approx(expected, rel_tol)


class _Approx:
    """Comparador de igualdad aproximada minimal, sin depender de pytest.approx
    para poder correr este archivo también con `python -m unittest` si hiciera falta."""

    def __init__(self, expected: float, rel_tol: float):
        self.expected = expected
        self.rel_tol = rel_tol

    def __eq__(self, other: float) -> bool:
        return math.isclose(other, self.expected, rel_tol=self.rel_tol)

    def __repr__(self) -> str:
        return f"≈{self.expected} (rel_tol={self.rel_tol})"


if __name__ == "__main__":
    test_ellipse_area_matches_analytical_formula()
    test_none_mask_returns_zero()
    test_empty_mask_returns_zero()
    test_degenerate_contour_returns_zero_not_exception()
    test_ratio_zero_when_iris_not_detected_no_zero_division()
    test_ratio_zero_when_pupil_not_detected()
    test_ratio_zero_when_neither_detected()
    test_ratio_normal_case()
    print("OK: test_geometry.py")
