# seca/closed_loop/update_sdwm.py
def update_sdwm(sdwm, optimizer, loss):
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()
