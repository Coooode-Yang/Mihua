"""Compatibility self-test for the retired growth feature and retained spelling."""
from .sedentary_smoke import run_sedentary_smoke


def run_growth_smoke(pet_factory, preview_dir=None):
    return run_sedentary_smoke(pet_factory, preview_dir)
