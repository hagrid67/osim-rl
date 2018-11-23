import math
import numpy as np
import os
from .utils.mygym import convert_to_gym
import gym
import opensim
import random
import pandas as pd

def floatstr(*lrVal):
    return " ".join(["{:.3f}".format(float(rVal)) for rVal in lrVal])


## OpenSim interface
# The main purpose of this class is to wrap all 
# the necessery elements of OpenSim in one place
# The actual RL environment then only needs to:
# - open a model
# - actuate
# - integrate
# - read the high level description of the state
# The objective, stop condition, and other gym-related
# methods are enclosed in the OsimEnv class
class OsimModel(object):
    # Initialize simulation
    stepsize = 0.01

    model = None
    state = None
    state0 = None
    joints = []
    bodies = []
    brain = None
    verbose = False
    istep = 0
    
    state_desc_istep = None
    prev_state_desc = None
    state_desc = None
    integrator_accuracy = None

    maxforces = []
    curforces = []

    def __init__(self, model_path, visualize, integrator_accuracy = 5e-5):
        self.integrator_accuracy = integrator_accuracy
        self.model = opensim.Model(model_path)
        self.model.initSystem()
        self.brain = opensim.PrescribedController()

        # Enable the visualizer
        self.model.setUseVisualizer(bool(visualize))

        self.muscleSet = self.model.getMuscles()
        self.forceSet = self.model.getForceSet()
        self.bodySet = self.model.getBodySet()
        self.jointSet = self.model.getJointSet()
        self.markerSet = self.model.getMarkerSet()
        self.contactGeometrySet = self.model.getContactGeometrySet()

        if self.verbose:
            self.list_elements()

        # Add actuators as constant functions. Then, during simulations
        # we will change levels of constants.
        # One actuartor per each muscle
        for j in range(self.muscleSet.getSize()):
            func = opensim.Constant(1.0)
            self.brain.addActuator(self.muscleSet.get(j))
            self.brain.prescribeControlForActuator(j, func)

            self.maxforces.append(self.muscleSet.get(j).getMaxIsometricForce())
            self.curforces.append(1.0)

        self.noutput = self.muscleSet.getSize()
            
        self.model.addController(self.brain)
        self.model.initSystem()

        self.dfKin = pd.read_csv("./input-data/1.25.csv")

    def list_elements(self):
        print("JOINTS")
        for i in range(self.jointSet.getSize()):
            print(i,self.jointSet.get(i).getName())
        print("\nBODIES")
        for i in range(self.bodySet.getSize()):
            print(i,self.bodySet.get(i).getName())
        print("\nMUSCLES")
        for i in range(self.muscleSet.getSize()):
            print(i,self.muscleSet.get(i).getName())
        print("\nFORCES")
        for i in range(self.forceSet.getSize()):
            print(i,self.forceSet.get(i).getName())
        print("\nMARKERS")
        for i in range(self.markerSet.getSize()):
            print(i,self.markerSet.get(i).getName())

    def actuate(self, action):
        if np.any(np.isnan(action)):
            raise ValueError("NaN passed in the activation vector. Values in [0,1] interval are required.")

        # TODO: Check if actions within [0,1]
        self.last_action = action
            
        brain = opensim.PrescribedController.safeDownCast(self.model.getControllerSet().get(0))
        functionSet = brain.get_ControlFunctions()

        for j in range(functionSet.getSize()):
            func = opensim.Constant.safeDownCast(functionSet.get(j))
            func.setValue( float(action[j]) )

    """
    Directly modifies activations in the current state.
    """
    def set_activations(self, activations):
        if np.any(np.isnan(activations)):
            raise ValueError("NaN passed in the activation vector. Values in [0,1] interval are required.")
        for j in range(self.muscleSet.getSize()):
            self.muscleSet.get(j).setActivation(self.state, activations[j])
        self.reset_manager()

    """
    Get activations in the given state.
    """
    def get_activations(self):
        return [self.muscleSet.get(j).getActivation(self.state) for j in range(self.muscleSet.getSize())]

    def compute_state_desc(self):
        self.model.realizeAcceleration(self.state)

        res = {}

        ## Joints
        res["joint_pos"] = {}
        res["joint_vel"] = {}
        res["joint_acc"] = {}
        for i in range(self.jointSet.getSize()):
            joint = self.jointSet.get(i)
            name = joint.getName()
            res["joint_pos"][name] = [joint.get_coordinates(i).getValue(self.state) for i in range(joint.numCoordinates())]
            res["joint_vel"][name] = [joint.get_coordinates(i).getSpeedValue(self.state) for i in range(joint.numCoordinates())]
            res["joint_acc"][name] = [joint.get_coordinates(i).getAccelerationValue(self.state) for i in range(joint.numCoordinates())]

        ## Bodies
        res["body_pos"] = {}
        res["body_vel"] = {}
        res["body_acc"] = {}
        res["body_pos_rot"] = {}
        res["body_vel_rot"] = {}
        res["body_acc_rot"] = {}
        for i in range(self.bodySet.getSize()):
            body = self.bodySet.get(i)
            name = body.getName()
            res["body_pos"][name] = [body.getTransformInGround(self.state).p()[i] for i in range(3)]
            res["body_vel"][name] = [body.getVelocityInGround(self.state).get(1).get(i) for i in range(3)]
            res["body_acc"][name] = [body.getAccelerationInGround(self.state).get(1).get(i) for i in range(3)]
            
            res["body_pos_rot"][name] = [body.getTransformInGround(self.state).R().convertRotationToBodyFixedXYZ().get(i) for i in range(3)]
            res["body_vel_rot"][name] = [body.getVelocityInGround(self.state).get(0).get(i) for i in range(3)]
            res["body_acc_rot"][name] = [body.getAccelerationInGround(self.state).get(0).get(i) for i in range(3)]

        ## Forces
        res["forces"] = {}
        for i in range(self.forceSet.getSize()):
            force = self.forceSet.get(i)
            name = force.getName()
            values = force.getRecordValues(self.state)
            res["forces"][name] = [values.get(i) for i in range(values.size())]

        ## Muscles
        res["muscles"] = {}
        for i in range(self.muscleSet.getSize()):
            muscle = self.muscleSet.get(i)
            name = muscle.getName()
            res["muscles"][name] = {}
            res["muscles"][name]["activation"] = muscle.getActivation(self.state)
            res["muscles"][name]["fiber_length"] = muscle.getFiberLength(self.state)
            res["muscles"][name]["fiber_velocity"] = muscle.getFiberVelocity(self.state)
            res["muscles"][name]["fiber_force"] = muscle.getFiberForce(self.state)
            # We can get more properties from here http://myosin.sourceforge.net/2125/classOpenSim_1_1Muscle.html 
        
        ## Markers
        res["markers"] = {}
        for i in range(self.markerSet.getSize()):
            marker = self.markerSet.get(i)
            name = marker.getName()
            res["markers"][name] = {}
            res["markers"][name]["pos"] = [marker.getLocationInGround(self.state)[i] for i in range(3)]
            res["markers"][name]["vel"] = [marker.getVelocityInGround(self.state)[i] for i in range(3)]
            res["markers"][name]["acc"] = [marker.getAccelerationInGround(self.state)[i] for i in range(3)]

        ## Other
        res["misc"] = {}
        res["misc"]["mass_center_pos"] = [self.model.calcMassCenterPosition(self.state)[i] for i in range(3)]
        res["misc"]["mass_center_vel"] = [self.model.calcMassCenterVelocity(self.state)[i] for i in range(3)]
        res["misc"]["mass_center_acc"] = [self.model.calcMassCenterAcceleration(self.state)[i] for i in range(3)]

        return res

    def get_state_desc(self):
        if self.state_desc_istep != self.istep:
            self.prev_state_desc = self.state_desc
            self.state_desc = self.compute_state_desc()
            self.state_desc_istep = self.istep
        return self.state_desc

    def set_strength(self, strength):
        self.curforces = strength
        for i in range(len(self.curforces)):
            self.muscleSet.get(i).setMaxIsometricForce(self.curforces[i] * self.maxforces[i])

    def get_body(self, name):
        return self.bodySet.get(name)

    def get_joint(self, name):
        return self.jointSet.get(name)

    def get_muscle(self, name):
        return self.muscleSet.get(name)

    def get_marker(self, name):
        return self.markerSet.get(name)

    def get_contact_geometry(self, name):
        return self.contactGeometrySet.get(name)

    def get_force(self, name):
        return self.forceSet.get(name)

    def get_action_space_size(self):
        return self.noutput

    def set_integrator_accuracy(self, integrator_accuracy):
        self.integrator_accuracy = integrator_accuracy

    def reset_manager(self):
        self.manager = opensim.Manager(self.model)
        self.manager.setIntegratorAccuracy(self.integrator_accuracy)
        self.manager.initialize(self.state)

    def reset(self):
        self.state = self.model.initializeState()
        self.model.equilibrateMuscles(self.state)

        self.istep = self.start_point =  0

        np.random.seed(None) 
        self.istep = self.start_point =  np.random.randint(-33,100)#10/11 for jrf plots, 50/51 for video
        if self.istep < 20:
            self.istep = self.start_point = 0
        
        init_states = self.dfKin.iloc[self.istep,2:].values
        vec = opensim.Vector(init_states)
        self.model.setStateVariableValues(self.state, vec)

        self.state.setTime(self.istep*self.stepsize)
        self.reset_manager()
        
        #self.state.setTime(0)
        #self.istep = 0

        #self.reset_manager()

    def get_state(self):
        return opensim.State(self.state)

    def set_state(self, state):
        self.state = state
        self.istep = int(self.state.getTime() / self.stepsize) # TODO: remove istep altogether
        self.reset_manager()

    def integrate(self):
        # Define the new endtime of the simulation
        self.istep = self.istep + 1

        # Integrate till the new endtime
        self.state = self.manager.integrate(self.stepsize * self.istep)


class Spec(object):
    def __init__(self, *args, **kwargs):
        self.id = 0
        self.timestep_limit = 300

## OpenAI interface
# The amin purpose of this class is to provide wrap all 
# the functions of OpenAI gym. It is still an abstract
# class but closer to OpenSim. The actual classes of
# environments inherit from this one and:
# - select the model file
# - define the rewards and stopping conditions
# - define an obsernvation as a function of state
class OsimEnv(gym.Env):
    action_space = None
    observation_space = None
    osim_model = None
    istep = 0
    verbose = False

    visualize = False
    spec = None
    time_limit = 1e10

    prev_state_desc = None

    model_path = None # os.path.join(os.path.dirname(__file__), '../models/MODEL_NAME.osim')    

    metadata = {
        'render.modes': ['human'],
        'video.frames_per_second' : None
    }

    def reward(self, gAct):
        raise NotImplementedError

    def is_done(self):
        return False

    def __init__(self, visualize = True, integrator_accuracy = 5e-5):
        self.visualize = visualize
        self.integrator_accuracy = integrator_accuracy
        self.load_model()

    def load_model(self, model_path = None):
        if model_path:
            self.model_path = model_path
            
        self.osim_model = OsimModel(self.model_path, self.visualize, integrator_accuracy = self.integrator_accuracy)

        # Create specs, action and observation spaces mocks for compatibility with OpenAI gym
        self.spec = Spec()
        self.spec.timestep_limit = self.time_limit

        self.action_space = ( [0.0] * self.osim_model.get_action_space_size(), [1.0] * self.osim_model.get_action_space_size() )
#        self.observation_space = ( [-math.pi*100] * self.get_observation_space_size(), [math.pi*100] * self.get_observation_space_s
        self.observation_space = ( [0] * self.get_observation_space_size(), [0] * self.get_observation_space_size() )
        
        self.action_space = convert_to_gym(self.action_space)
        self.observation_space = convert_to_gym(self.observation_space)

    def get_state_desc(self):
        return self.osim_model.get_state_desc()

    def get_prev_state_desc(self):
        return self.prev_state_desc

    def get_observation(self):
        # This one will normally be overwrtitten by the environments
        # In particular, for the gym we want a vector and not a dictionary
        return self.osim_model.get_state_desc()

    def get_observation_space_size(self):
        return 0

    def get_action_space_size(self):
        return self.osim_model.get_action_space_size()

    def reset(self, project = True):
        self.osim_model.reset()
        
        if not project:
            return self.get_state_desc()
        return self.get_observation()

    def step(self, action, project = True):
        self.prev_state_desc = self.get_state_desc()        
        self.osim_model.actuate(action)
        self.osim_model.integrate()

        if project:
            obs = self.get_observation()
        else:
            obs = self.get_state_desc()
            
        return [ obs, self.reward(action), self.is_done() or (self.osim_model.istep >= self.spec.timestep_limit), {} ]

    def render(self, mode='human', close=False):
        return

class L2RunEnv(OsimEnv):
    model_path = os.path.join(os.path.dirname(__file__), '../models/gait9dof18musc.osim')    
    time_limit = 1000

    def is_done(self):
        state_desc = self.get_state_desc()
        return state_desc["body_pos"]["pelvis"][1] < 0.6

    ## Values in the observation vector
    def get_observation(self):
        state_desc = self.get_state_desc()

        # Augmented environment from the L2R challenge
        res = []
        pelvis = None

        res += state_desc["joint_pos"]["ground_pelvis"]
        res += state_desc["joint_vel"]["ground_pelvis"]

        for joint in ["hip_l","hip_r","knee_l","knee_r","ankle_l","ankle_r",]:
            res += state_desc["joint_pos"][joint]
            res += state_desc["joint_vel"][joint]

        for body_part in ["head", "pelvis", "torso", "toes_l", "toes_r", "talus_l", "talus_r"]:
            res += state_desc["body_pos"][body_part][0:2]

        res = res + state_desc["misc"]["mass_center_pos"] + state_desc["misc"]["mass_center_vel"]

        res += [0]*5

        return res

    def get_observation_space_size(self):
        return 41

    def reward(self):
        state_desc = self.get_state_desc()
        prev_state_desc = self.get_prev_state_desc()
        if not prev_state_desc:
            return 0
        return state_desc["joint_pos"]["ground_pelvis"][1] - prev_state_desc["joint_pos"]["ground_pelvis"][1]

def rect(row):
    r = row[0]
    theta = row[1]
    x = r * math.cos(theta)
    y = 0
    z = r * math.sin(theta)
    return np.array([x,y,z])

class ProstheticsEnv(OsimEnv):
    prosthetic = True
    model = "3D"
    def get_model_key(self):
        return self.model + ("_pros" if self.prosthetic else "")

    def set_difficulty(self, difficulty):
        self.difficulty = difficulty
        
        #if difficulty == 0:
        #    self.time_limit = 300
        #if difficulty == 1:
        #    self.time_limit = 1000

        # jw set timestep_limit from dConfig
        self.time_limit = self.spec.timestep_limit = self.dConfig["timestep_limit"]
        #self.spec.timestep_limit = self.time_limit    

    def __init__(self,
            visualize = True, 
            integrator_accuracy = 5e-5,
            difficulty=0,
            seed=0,
            dEnvConfig={},
            ):


        # default values to be overridden by runner in dEnvConfig
        self.dConfigDefault = dict(
            visualize = None, # ie use function arg unless overridden
            rBaseReward = 10.,
            rPenPelvisRot = 0.,
            rPenHipAdd = 0.,
            rHipAddThresh = 0.,
            rPenKneeStraight = 0.,
            rKneeThresh = 0.,
            rPenPower = 2., # only applied to velocity penalty.
            timestep_limit = 1000,
            debug = False,
            sfOsim = None,
            

            )
        self.dConfig = {**(self.dConfigDefault), **dEnvConfig} # overwrite default with dEnvConfig
        self.rBaseReward = self.dConfig["rBaseReward"]

        # override the function arg if set in dConfig
        if ("visualize" in self.dConfig) and (self.dConfig["visualize"] is not None):
            visualize = self.dConfig["visualize"]


        self.model_paths = {}
        self.model_paths["3D_pros"] = os.path.join(os.path.dirname(__file__), '../models/gait14dof22musc_pros_20180507.osim')    
        self.model_paths["3D"] = os.path.join(os.path.dirname(__file__), '../models/gait14dof22musc_20170320.osim')    
        self.model_paths["2D_pros"] = os.path.join(os.path.dirname(__file__), '../models/gait14dof22musc_planar_pros_20180507.osim')    
        self.model_paths["2D"] = os.path.join(os.path.dirname(__file__), '../models/gait14dof22musc_planar_20170320.osim')

        if self.dConfig["sfOsim"] is not None:
            self.model_path = os.path.join(os.path.dirname(__file__), "../models/" + self.dConfig["sfOsim"])
        else:
            self.model_path = self.model_paths[self.get_model_key()]

        super(ProstheticsEnv, self).__init__(visualize = visualize, integrator_accuracy = integrator_accuracy)
        
        self.set_difficulty(difficulty)
        
        random.seed(seed)
        self.dfKin = self.osim_model.dfKin
        



    def change_model(self, model='3D', prosthetic=True, difficulty=0, seed=0):
        if (self.model, self.prosthetic) != (model, prosthetic):
            self.model, self.prosthetic = model, prosthetic
            self.load_model(self.model_paths[self.get_model_key()])
        self.set_difficulty(difficulty)
        random.seed(seed)
    
    def is_done(self):
        state_desc = self.get_state_desc()
        return state_desc["body_pos"]["pelvis"][1] < 0.6

    ## Values in the observation vector
    # y, vx, vy, ax, ay, rz, vrz, arz of pelvis (8 values)
    # x, y, vx, vy, ax, ay, rz, vrz, arz of head, torso, toes_l, toes_r, talus_l, talus_r (9*6 values)
    # rz, vrz, arz of ankle_l, ankle_r, back, hip_l, hip_r, knee_l, knee_r (7*3 values)
    # activation, fiber_len, fiber_vel for all muscles (3*18)
    # x, y, vx, vy, ax, ay ofg center of mass (6)
    # 8 + 9*6 + 8*3 + 3*18 + 6 = 146
    def get_observation(self):
        state_desc = self.get_state_desc()

        # Augmented environment from the L2R challenge
        res = []
        pelvis = None

        for body_part in ["pelvis", "head","torso","toes_l","toes_r","talus_l","talus_r"]:
            if self.prosthetic and body_part in ["toes_r","talus_r"]:
                res += [0] * 9
                continue
            cur = []
            cur += state_desc["body_pos"][body_part][0:2]
            cur += state_desc["body_vel"][body_part][0:2]
            cur += state_desc["body_acc"][body_part][0:2]
            cur += state_desc["body_pos_rot"][body_part][2:]
            cur += state_desc["body_vel_rot"][body_part][2:]
            cur += state_desc["body_acc_rot"][body_part][2:]
            if body_part == "pelvis":
                pelvis = cur  # save the pelvis coords - pos, vel, acc, etc
                res += cur[1:] # leave out the x position of the pelvis... dumb with target vel now vector?
                # should we subtract both x and z?
                # and leave out x and z position of pelvis?
            else:
                cur_upd = cur # copy the refence to cur...
                cur_upd[:2] = [cur[i] - pelvis[i] for i in range(2)] # subtract the pelvis x,y?
                cur_upd[6:7] = [cur[i] - pelvis[i] for i in range(6,7)] # just affects item 6 - body_pos_rot x?
                res += cur

        for joint in ["ankle_l","ankle_r","back","hip_l","hip_r","knee_l","knee_r"]:
            res += state_desc["joint_pos"][joint]
            res += state_desc["joint_vel"][joint]
            res += state_desc["joint_acc"][joint]

        for muscle in sorted(state_desc["muscles"].keys()):
            res += [state_desc["muscles"][muscle]["activation"]]
            res += [state_desc["muscles"][muscle]["fiber_length"]]
            res += [state_desc["muscles"][muscle]["fiber_velocity"]]

        cm_pos = [state_desc["misc"]["mass_center_pos"][i] - pelvis[i] for i in range(2)]
        res = res + cm_pos + state_desc["misc"]["mass_center_vel"] + state_desc["misc"]["mass_center_acc"]
        # jw add target vel
        #d["target_vel"] = self.targets[self.osim_model.istep,:].tolist()
        #print(len(res))

        # Add the target vel x and z coords to the observation list
        res += [ state_desc["target_vel"][i] for i in [0,2] ]
        return res

    def get_observation_space_size(self):
        if self.prosthetic == True:
            # return 160
            return 162 # added target_vel x,z
        return 169

    def generate_new_targets(self, poisson_lambda = 300):
        nsteps = self.time_limit + 1
        rg = np.array(range(nsteps))
        velocity = np.zeros(nsteps)
        heading = np.zeros(nsteps)

        velocity[0] = 1.25
        heading[0] = 0

        change = np.cumsum(np.random.poisson(poisson_lambda, 10))

        for i in range(1,nsteps):
            velocity[i] = velocity[i-1]
            heading[i] = heading[i-1]

            if i in change:
                velocity[i] += random.choice([-1,1]) * random.uniform(-0.5,0.5)
                heading[i] += random.choice([-1,1]) * random.uniform(-math.pi/8,math.pi/8)

        trajectory_polar = np.vstack((velocity,heading)).transpose()
        self.targets = np.apply_along_axis(rect, 1, trajectory_polar)
        #print (self.targets)
        
    def get_state_desc(self):
        d = super(ProstheticsEnv, self).get_state_desc()
        if self.difficulty > 0:
            d["target_vel"] = self.targets[self.osim_model.istep,:].tolist()
        return d

    def reset(self, project = True):
        self.generate_new_targets()
        return super(ProstheticsEnv, self).reset(project = project)

        #self.iStart = max(0, np.random.randint(low=-60, high=100))
        #self.state = self.osim_model.model.initializeState()
        #self.istep = self.start_point =  0
        #self.istep = self.start_point =  self.iStart # np.random.randint(0,100)#10/11 for jrf plots, 50/51 for video
        #gState = self.dfKin.iloc[self.istep,2:].values
        #oVecState = opensim.Vector(gState)
        #self.osim_model.model.setStateVariableValues(self.state, oVecState)

        #self.osim_model.state.setTime(self.istep*self.stepsize)
        #self.reset_manager()


        #speed_reward = np.exp(-0.1*total_speed_loss)
        # pos_reward = np.exp(-2* total_position_loss)


    def reward_round1(self, gAct):
        state_desc = self.get_state_desc()
        prev_state_desc = self.get_prev_state_desc()
        if not prev_state_desc:
            return 0
        return 9.0 - (state_desc["body_vel"]["pelvis"][0] - 3.0)**2

    def reward_round2(self, gAct):
        state_desc = self.get_state_desc()
        prev_state_desc = self.get_prev_state_desc()
        penalty = 0
        rPenPower = self.dConfig["rPenPower"]

        # Small penalty for too much activation (cost of transport)
        penalty += np.sum(np.array(self.osim_model.get_activations())**2) * 0.001

        # Big penalty for not matching the vector on the X,Z projection.
        # No penalty for the vertical axis
        #penalty += (state_desc["body_vel"]["pelvis"][0] - state_desc["target_vel"][0])**2
        #penalty += (state_desc["body_vel"]["pelvis"][2] - state_desc["target_vel"][2])**2
        
        rPenVx = abs(state_desc["body_vel"]["pelvis"][0] - state_desc["target_vel"][0])**rPenPower
        rPenVz = abs(state_desc["body_vel"]["pelvis"][2] - state_desc["target_vel"][2])**rPenPower
        
        # jw reward shaping
        # penalty for rotating the pelvis (to encourage "normal" walking)
        gPelvRot = np.array(state_desc["body_pos_rot"]["pelvis"])
        rPelvRot = np.sum(gPelvRot ** 2) # leave rPenPower out here.
        penalty += self.dConfig["rPenPelvisRot"] * rPelvRot

        # Hip Adduction: >0 means leg pulled inwards.
        rHipAdd_l = state_desc["joint_pos"]["hip_l"][1] - self.dConfig["rHipAddThresh"]
        rHipAdd_r = state_desc["joint_pos"]["hip_r"][1] - self.dConfig["rHipAddThresh"]

        rHipAdd_l *= (np.sign(rHipAdd_l) > 0)  # ie positive part: zero if negative, 
        rHipAdd_r *= (np.sign(rHipAdd_r) > 0) 

        rPenHipAdd = self.dConfig["rPenHipAdd"] * ((rHipAdd_l + rHipAdd_r) ** 1) # not rPenPower

        rKnee_l = state_desc["joint_pos"]["knee_l"][0] - self.dConfig["rKneeThresh"]
        rKnee_r = state_desc["joint_pos"]["knee_r"][0] - self.dConfig["rKneeThresh"]

        rKnee_l *= (np.sign(rKnee_l) > 0)
        rKnee_r *= (np.sign(rKnee_r) > 0)

        rPenKneeStraight = self.dConfig["rPenKneeStraight"] * ((rKnee_l + rKnee_r) ** 1) # not rPenPower

        # lance
        # reward = b1 x "standard reward" + b2 x ( c2 x kin vel reward + c3 x kin pos reward),
        # where c2, c3 = 0.25, 0.75,
        # when (istep > 20) => b1, b2 = 0.3, 0.7 
        # when istep < 20   =>          0.9, 0.1 
        dfKin = self.osim_model.dfKin
        t = self.osim_model.istep

        # blending of target velocity and target kinetics
        if t < 20:
            rkTarVel, rkKin = 0.9, 0.1
        else:
            rkTarVel, rkKin  = 0.3, 0.7

        it = t
        if it > 1000:
            rkTarVel, rkKin = 1, 0
            it = 1000

        rkKinVel = 0.25
        rkKinPos = 0.75


        ankle_loss = (state_desc['joint_pos']['ankle_l'] - dfKin['ankle_angle_l'][it])**2

        knee_loss = (state_desc['joint_pos']['knee_l'] - dfKin['knee_angle_l'][it])**2 + \
                    (state_desc['joint_pos']['knee_r'] - dfKin['knee_angle_r'][it])**2

        hip_loss =  (state_desc['joint_pos']['hip_l'][0] - dfKin['hip_flexion_l'][it])**2 +      \
                    (state_desc['joint_pos']['hip_r'][0] - dfKin['hip_flexion_r'][it])**2 +      \
                    (state_desc['joint_pos']['hip_l'][1] - dfKin['hip_adduction_l'][it])**2 +    \
                    (state_desc['joint_pos']['hip_r'][1] - dfKin['hip_adduction_r'][it])**2


        ankle_loss_v = (state_desc['joint_vel']['ankle_l'] - dfKin['ankle_angle_l_speed'][it])**2 
                    #+ (state_desc['joint_vel']['ankle_r'] - dfKin['ankle_angle_r_speed'][it])**2
        
        knee_loss_v = (state_desc['joint_vel']['knee_l'] - dfKin['knee_angle_l_speed'][it])**2 +     \
                     (state_desc['joint_vel']['knee_r'] - dfKin['knee_angle_r_speed'][it])**2

        hip_loss_v = (state_desc['joint_vel']['hip_l'][0] - dfKin['hip_flexion_l_speed'][it])**2 +   \
                     (state_desc['joint_vel']['hip_r'][0] - dfKin['hip_flexion_r_speed'][it])**2 +   \
                     (state_desc['joint_vel']['hip_l'][1] - dfKin['hip_adduction_l_speed'][it])**2 + \
                     (state_desc['joint_vel']['hip_r'][1] - dfKin['hip_adduction_r_speed'][it])**2

        rLossPos = ankle_loss + knee_loss + hip_loss
        rLossVel = ankle_loss_v + knee_loss_v + hip_loss_v

        #rRewStatePos = float(np.exp(-self.dConfig["rPenStateLoc"] * rLossPos))
        #rRewStateVel = float(np.exp(-self.dConfig["rPenStateVel"] * rLossVel))
        rRewKinPos = float(np.exp(-2 * rLossPos))
        rRewKinVel = float(np.exp(-0.1 * rLossVel))

        # my modification - drop the exponential, just use the loss ie distance^2
        #rPenKinPos = rkKinPos * rLossPos
        #rPenKinVel = rkKinVel * min(0.1 * rLossVel, 2) # cap the vel loss

        # lance 
        rLossTarVel =   (state_desc["body_vel"]['pelvis'][0] - state_desc["target_vel"][0])**2 + \
                        (state_desc["body_vel"]['pelvis'][2] - state_desc["target_vel"][2])**2 
        rRewTarVel = np.exp(-8 * rLossTarVel)



        #print(rRewStatePos, rRewStateVel, type(rRewStatePos), type(rRewStateVel))


        #penalty += rPenVx + rPenVz + rPenHipAdd + rPenKneeStraight + rPenKinPos + rPenKinVel
        #penalty = 0


        # Reward for not falling
        #reward = 10.0
        #reward = self.rBaseReward + rRewStatePos + rRewStateVel
        reward = self.rBaseReward + rkTarVel * rRewTarVel + rkKin * ( rkKinPos * rRewKinPos + rkKinVel * rRewKinVel)
        #reward = self.rBaseReward - float(penalty)


        if self.dConfig["debug"]:
            print(os.getpid(), t,
                floatstr(reward-penalty,
                reward,
                penalty, 
                self.rBaseReward,
                rkTarVel,
                rRewTarVel,
                rkKin,
                rkKinPos,
                rRewKinPos,
                rkKinVel,
                rRewKinVel,
                ),
                " ".join( [ str(oAct[0]) for oAct in gAct ] ),
                )

        if False:
            print(
                os.getpid(), t,
                "rew:", floatstr(reward),
                "Pen:", floatstr(penalty),
                #"PenV:", floatstr(rPenVx, rPenVz),
                "PenHip:", floatstr(rPenHipAdd, rHipAdd_l, rHipAdd_r),
                #"Knee", floatstr(rPenKneeStraight, rKnee_l, rKnee_r),
                "PelvRot:", floatstr(rPelvRot),
                "KinPos:", floatstr(rPenKinPos),
                "KinVel:", floatstr(rPenKinVel),
                "lossv:", floatstr(ankle_loss_v, knee_loss_v, hip_loss_v),
                #"v", state_desc['joint_vel']['ankle_l'], dfKin['ankle_angle_l_speed'][it],
                )


        

        if False: # if self.dConfig["debug"]:
            print(
                t,
                "Rew:", floatstr(reward),
                "TarVxz:", floatstr(*(state_desc["target_vel"])),
                "RewTarV:", floatstr(rRewTarVel),
                "rkKin", floatstr(rkKin),

                "rRewKinPos:", floatstr(rRewKinPos),
                "rRewKinVel:", floatstr(rRewKinVel),
                #"Knee", floatstr(rPenKneeStraight, rKnee_l, rKnee_r),
                #"PelvRot:", floatstr(rPelvRot),
                #"RewPos:", floatstr(rRewStatePos),
                #"RewVel:", floatstr(rRewStateVel),
                "lossv:", floatstr(ankle_loss_v, knee_loss_v, hip_loss_v),
                "v", state_desc['joint_vel']['ankle_l'], dfKin['ankle_angle_l_speed'][it],
                )


        return reward - penalty 
        #return reward

    def reward(self, gAct):
        if self.difficulty == 0:
            return self.reward_round1(gAct)
        return self.reward_round2(gAct)


class Arm2DEnv(OsimEnv):
    model_path = os.path.join(os.path.dirname(__file__), '../models/arm2dof6musc.osim')    
    time_limit = 200
    target_x = 0
    target_y = 0

    def get_observation(self):
        state_desc = self.get_state_desc()

        res = [self.target_x, self.target_y]

        # for body_part in ["r_humerus", "r_ulna_radius_hand"]:
        #     res += state_desc["body_pos"][body_part][0:2]
        #     res += state_desc["body_vel"][body_part][0:2]
        #     res += state_desc["body_acc"][body_part][0:2]
        #     res += state_desc["body_pos_rot"][body_part][2:]
        #     res += state_desc["body_vel_rot"][body_part][2:]
        #     res += state_desc["body_acc_rot"][body_part][2:]

        for joint in ["r_shoulder","r_elbow",]:
            res += state_desc["joint_pos"][joint]
            res += state_desc["joint_vel"][joint]
            res += state_desc["joint_acc"][joint]

        for muscle in sorted(state_desc["muscles"].keys()):
            res += [state_desc["muscles"][muscle]["activation"]]
            # res += [state_desc["muscles"][muscle]["fiber_length"]]
            # res += [state_desc["muscles"][muscle]["fiber_velocity"]]

        res += state_desc["markers"]["r_radius_styloid"]["pos"][:2]

        return res

    def get_observation_space_size(self):
        return 16 #46

    def generate_new_target(self):
        theta = random.uniform(math.pi*9/8, math.pi*12/8)
        radius = random.uniform(0.5, 0.65)
        self.target_x = math.cos(theta) * radius 
        self.target_y = math.sin(theta) * radius

        state = self.osim_model.get_state()

#        self.target_joint.getCoordinate(0).setValue(state, self.target_x, False)
        self.target_joint.getCoordinate(1).setValue(state, self.target_x, False)

        self.target_joint.getCoordinate(2).setLocked(state, False)
        self.target_joint.getCoordinate(2).setValue(state, self.target_y, False)
        self.target_joint.getCoordinate(2).setLocked(state, True)
        self.osim_model.set_state(state)
        
    def reset(self, random_target = True):
        obs = super(Arm2DEnv, self).reset()
        if random_target:
            self.generate_new_target()
        self.osim_model.reset_manager()
        return obs

    def __init__(self, *args, **kwargs):
        super(Arm2DEnv, self).__init__(*args, **kwargs)
        blockos = opensim.Body('target', 0.0001 , opensim.Vec3(0), opensim.Inertia(1,1,.0001,0,0,0) );
        self.target_joint = opensim.PlanarJoint('target-joint',
                                  self.osim_model.model.getGround(), # PhysicalFrame
                                  opensim.Vec3(0, 0, 0),
                                  opensim.Vec3(0, 0, 0),
                                  blockos, # PhysicalFrame
                                  opensim.Vec3(0, 0, -0.25),
                                  opensim.Vec3(0, 0, 0))

        geometry = opensim.Ellipsoid(0.02, 0.02, 0.02);
        geometry.setColor(opensim.Green);
        blockos.attachGeometry(geometry)

        self.osim_model.model.addJoint(self.target_joint)
        self.osim_model.model.addBody(blockos)
        
        self.osim_model.model.initSystem()
    
    def reward(self):
        state_desc = self.get_state_desc()
        penalty = (state_desc["markers"]["r_radius_styloid"]["pos"][0] - self.target_x)**2 + (state_desc["markers"]["r_radius_styloid"]["pos"][1] - self.target_y)**2
        # print(state_desc["markers"]["r_radius_styloid"]["pos"])
        # print((self.target_x, self.target_y))
        return 1.-penalty
