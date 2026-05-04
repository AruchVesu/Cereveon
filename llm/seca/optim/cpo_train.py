# seca/optim/cpo_train.py
import torch
import torch.nn.functional as F


def cpo_train_step(sdwm, policy, z_batch, optimizer):
    """
    One gradient step for Curriculum Policy Optimizer.
    """
    total_loss = 0.0

    for z0 in z_batch:
        z0 = z0.unsqueeze(0)

        reward, traj = rollout(sdwm, policy, z0)

        # value prediction
        _, value = policy(z0)

        advantage = reward.detach() - value.squeeze()

        # policy loss (maximize reward)
        policy_loss = -advantage

        # value loss
        value_loss = F.mse_loss(value.squeeze(), reward.detach())

        loss = policy_loss + value_loss

        total_loss += loss

    total_loss /= len(z_batch)

    optimizer.zero_grad()
    total_loss.backward()
    optimizer.step()

    return total_loss.item()
