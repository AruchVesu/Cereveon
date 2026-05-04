# seca/closed_loop/error.py
import torch


def prediction_error(sdwm, z_t, action, z_real_next):
    """
    Compare predicted vs real skill evolution.
    """
    z_pred_next, _ = sdwm(z_t, action)

    error = z_real_next - z_pred_next
    loss = torch.mean(error**2)

    return loss, error
