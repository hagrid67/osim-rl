from osim.env import ProstheticsEnv
import traceback

import pprint, socket


def flattenddict(dObs, nLevel=0):
    lObs=[]
    if nLevel>0:
        print(nLevel, len(dObs.keys()))
    for sKey in dObs.keys():
        oVal = dObs[sKey]
        if type(oVal) is dict:
            lObs.extend(flattenddict(oVal, nLevel=nLevel+1))
        elif type(oVal) is list:
            for iVal, oVal2 in enumerate(oVal):
                if type(oVal2) is float:
                    lObs.append(oVal2)
                else:
                    print("Bad type in list:", sKey, iVal, type(oVal2), oVal2)
            lObs += oVal
        elif type(oVal) is float:
            lObs.extend([oVal])
        elif type(oVal) is type(None):
            lObs.extend(0.0)
        else:
            print ("bad type key {}".format(sKey), type(oVal), oVal)
    return lObs


try:
    
    sHost = socket.gethostname()
    bVis = (sHost == "jwpc12")
    #e = RunEnv(visualize=bVis)
    print("Creating Env...")
    env = ProstheticsEnv(visualize=bVis)
    print("Changing Env...")
    env.change_model(model='3D', prosthetic=False, difficulty=2, seed=None)
    print("Env done")


except Exception as e:
    print('error on start of standalone')
    traceback.print_exc()





observation = env.reset()
for i in range(300):
    observation, reward, done, info = env.step(env.action_space.sample(), project = False)
    print(type(observation), len(observation))
    #pprint.pprint(observation)
    #print (" ".join(["{:.2f}".format(rObs) for rObs in observation]))
    lObs = flattenddict(observation)
    print(len(lObs))
    #print(len(env.action_space.sample()))
    if done:
        env.reset()
