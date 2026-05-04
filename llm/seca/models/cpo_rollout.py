# seca/optim/cpo_rollout.py
import torch


def rollout(sdwm, policy, z0, horizon=10, gamma=0.99):
    """
    Simulate future training trajectory.
    """
    z = z0
    total_reward = 0.0
    discount = 1.0

    traj = []

    for _ in range(horizon):
        logits, _ = policy(z)
        a = torch.tanh(logits)  # continuous action embedding

        z_next, _ = sdwm(z, a)

        reward = (z_next - z).norm(dim=-1)  # proxy for Elo gain

        total_reward += discount * reward
        discount *= gamma

        traj.append((z, a, reward))

        z = z_next

    return total_reward, traj
