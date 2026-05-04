# seca/closed_loop/outcome.py
import torch


def compute_real_delta(encoder, games_t, games_t1):
    """
    Encode skill before and after training window.
    """
    z_t = encoder(games_t)
    z_t1 = encoder(games_t1)

    return z_t1 - z_t, z_t1
