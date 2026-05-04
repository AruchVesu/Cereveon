import numpy as np
from pathlib import Path

OUTPUT_PATH = Path("data/skill_dataset.npz")


def generate_player_transitions(length: int = 40):
    """
    Generates RL-style transitions:
    state, action, next_state, reward
    """

    skill = np.clip(np.random.normal(1200, 200), 600, 2400)

    states = []
    actions = []
    next_states = []
    rewards = []

    for _ in range(length):
        # action = training intensity / game difficulty signal
        action = np.random.uniform(-1, 1)

        improvement = np.random.normal(5, 10) + action * 8

        if skill < 1400:
            improvement += np.random.normal(5, 5)

        if skill > 1800:
            improvement -= np.random.normal(4, 4)

        new_skill = np.clip(skill + improvement, 600, 2600)

        reward = np.clip(
            (new_skill - skill) / 50 + np.random.normal(0, 0.05),
            -1,
            1,
        )

        states.append([skill])
        actions.append([action])
        next_states.append([new_skill])
        rewards.append([reward])

        skill = new_skill

    return (
        np.array(states),
        np.array(actions),
        np.array(next_states),
        np.array(rewards),
    )


def build_dataset(num_players: int = 2000, seq_len: int = 40):
    S, A, NS, R = [], [], [], []

    for _ in range(num_players):
        s, a, ns, r = generate_player_transitions(seq_len)
        S.append(s)
        A.append(a)
        NS.append(ns)
        R.append(r)

    return (
        np.concatenate(S),
        np.concatenate(A),
        np.concatenate(NS),
        np.concatenate(R),
    )


def main():
    print("Generating RL-style SECA skill dataset...")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    states, actions, next_states, rewards = build_dataset()

    np.savez(
        OUTPUT_PATH,
        states=states.astype(np.float32),
        actions=actions.astype(np.float32),
        next_states=next_states.astype(np.float32),
        rewards=rewards.astype(np.float32),
    )

    print("Saved →", OUTPUT_PATH.resolve())
    print("States shape:", states.shape)


if __name__ == "__main__":
    main()
