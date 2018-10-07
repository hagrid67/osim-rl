import opensim as osim
from osim.http.client import Client
from osim.env import ProstheticsEnv
import numpy as np
import argparse
import json
# Settings
# remote_base = 'http://grader.crowdai.org:1729' # Submission to Round-1
#remote_base = 'http://grader.crowdai.org:1730' # Submission to Round-2
remote_base = 'http://localhost:8050' # Submission to Round-2

# Command line parameters
parser = argparse.ArgumentParser(description='Submit the result to crowdAI')
parser.add_argument('--token', dest='token', action='store', required=True)
args = parser.parse_args()

print("create client")
client = Client(remote_base)
print("created client")

# Create environment
print("create client env")
observation = client.env_create(args.token, env_id="ProstheticsEnv")
print ("create env")
env = ProstheticsEnv()
print("created env")
# Run a single step
# The grader runs 3 simulations of at most 1000 steps each. We stop after the last one
while True:
    print(type(observation), len(observation))
    with open("obs-dict.txt", "a") as fOut:
        json.dump(observation, fOut)
        fOut.write("\n")
    [observation, reward, done, info] = client.env_step(env.action_space.sample().tolist())
    print(reward)
    if done:
        observation = client.env_reset()
        if not observation:
            break
            
client.submit()
