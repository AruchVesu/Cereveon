# seca/closed_loop/engine.py
def clale_step(
    encoder,
    sdwm,
    policy,
    sdwm_opt,
    games_before,
    games_after,
):
    """
    One real-world adaptation cycle.
    """

    # current latent
    z_t = encoder(games_before)

    # chosen training action
    logits, _ = policy(z_t)
    action = torch.tanh(logits)

    # real outcome
    delta_real, z_real_next = compute_real_delta(encoder, games_before, games_after)

    # prediction error
    loss, error = prediction_error(sdwm, z_t, action, z_real_next)

    # update world model
    update_sdwm(sdwm, sdwm_opt, loss)

    # adjust future curriculum
    new_action = adjust_curriculum(action, error)

    return {
        "loss": loss.item(),
        "error_norm": error.norm().item(),
        "new_action": new_action.detach(),
    }
