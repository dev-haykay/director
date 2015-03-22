import os
import sys
import vtkAll as vtk
from ddapp import botpy
import math
import time
import types
import functools
import numpy as np

from ddapp import transformUtils
from ddapp import lcmUtils
from ddapp.timercallback import TimerCallback
from ddapp.asynctaskqueue import AsyncTaskQueue
from ddapp import objectmodel as om
from ddapp import visualization as vis
from ddapp import applogic as app
from ddapp.debugVis import DebugData
from ddapp import ik
from ddapp import ikplanner
from ddapp import ioUtils
from ddapp.simpletimer import SimpleTimer
from ddapp.utime import getUtime
from ddapp import affordanceitems
from ddapp import robotstate
from ddapp import robotplanlistener
from ddapp import segmentation
from ddapp import planplayback
from ddapp import propertyset

import ddapp.tasks.robottasks as rt
import ddapp.tasks.taskmanagerwidget as tmw

import drc as lcmdrc

from PythonQt import QtCore, QtGui


class ValvePlannerDemo(object):

    def __init__(self, robotModel, footstepPlanner, manipPlanner, ikPlanner, lhandDriver, rhandDriver, atlasDriver, multisenseDriver, affordanceFitFunction, sensorJointController, planPlaybackFunction, showPoseFunction):
        self.robotModel = robotModel
        self.footstepPlanner = footstepPlanner
        self.manipPlanner = manipPlanner
        self.ikPlanner = ikPlanner
        self.lhandDriver = lhandDriver
        self.rhandDriver = rhandDriver
        self.atlasDriver = atlasDriver
        self.multisenseDriver = multisenseDriver
        self.affordanceFitFunction = affordanceFitFunction
        self.sensorJointController = sensorJointController
        self.planPlaybackFunction = planPlaybackFunction
        self.showPoseFunction = showPoseFunction
        self.graspingObject='valve'
        self.graspingHand='left'
        self.valveAffordance = None

        # live operation flags
        self.useFootstepPlanner = True
        self.visOnly = True
        self.planFromCurrentRobotState = True
        useDevelopment = False
        if (useDevelopment):
            self.visOnly = True
            self.planFromCurrentRobotState = False

        self.optionalUserPromptEnabled = False
        self.requiredUserPromptEnabled = True
        self.constraintSet = None

        self.plans = []

        self.faceTransformLocal = None
        self.facePath = []

        self.scribeInAir = False
        self.palmInAngle = 30 # how much should the palm face the axis - 0 not at all, 90 entirely
        self.scribeRadius = None
        self.useLidar = True # else use stereo depth

        # IK server speed:
        self.speedLow = 5
        self.speedHigh = 30

        if (useDevelopment): # for simulated dev
            self.speedLow = 60
            self.speedHigh = 60

        # reach to center and back - for palm point
        self.clenchFrameXYZ = [0.0, 0.0, -0.1]
        self.clenchFrameRPY = [90, 0, 180]
        self.reachDepth = -0.12 # distance away from valve for palm face on approach reach
        self.touchDepth = -0.06 # distance away from valve for palm face on approach reach

        # top level switch between BDI (locked base) and MIT (moving base and back)
        self.lockBack = False
        self.lockBase = False

        self.setupStance()

        self._setupSubscriptions()


    def _setupSubscriptions(self):
        sub0 = lcmUtils.addSubscriber('AUTONOMOUS_TEST_VALVE', lcmdrc.utime_t, self.autonomousTest)

    def setupStance(self):

        if (self.graspingObject == 'valve'):
            self.nextScribeAngleInitial = -60 # reach 60 degrees left of the valve spoke
            self.turnAngle=60
            #if self.scribeInAir:
            #    self.relativeStanceXYZInitial = [-0.6, -0.2, 0.0] # stand further away when scribing in air
            #else:
            #    self.relativeStanceXYZInitial = [-0.48, -0.2, 0.0]
            #self.relativeStanceRPYInitial = [0, 0, 16]

            self.relativeStanceXYZInitial = [-1.05, 0.27, 0.0]
            self.relativeStanceRPYInitial = [0, 0, 0.1]

        else:
            self.nextScribeAngleInitial = 0 # reach right into the valve axis
            self.turnAngle=90
            if self.scribeInAir:
                self.relativeStanceXYZInitial = [-0.6, -0.4, 0.0] # stand further away when scribing in air
            else:
                self.relativeStanceXYZInitial = [-0.48, -0.4, 0.0]
            self.relativeStanceRPYInitial = [0, 0, 16]

        if (self.graspingHand is 'left'): # -1 = anticlockwise (left, default) | 1 = clockwise
            self.scribeDirection = -1
        else:
            self.scribeDirection = 1

    def setNextScribeAngle(self, nextScribeAngle):
        self.nextScribeAngle = nextScribeAngle

    def resetTurnPath(self):
        for obj in om.getObjects():
            if obj.getProperty('Name') == 'face frame desired':
                om.removeFromObjectModel(obj)
        for obj in om.getObjects():
            if obj.getProperty('Name') == 'face frame desired path':
                om.removeFromObjectModel(obj)

    def addPlan(self, plan):
        self.plans.append(plan)

    def computeGroundFrame(self, robotModel):
        '''
        Given a robol model, returns a vtkTransform at a position between
        the feet, on the ground, with z-axis up and x-axis aligned with the
        robot pelvis x-axis.
        '''
        t1 = robotModel.getLinkFrame('l_foot')
        t2 = robotModel.getLinkFrame('r_foot')
        pelvisT = robotModel.getLinkFrame('pelvis')

        xaxis = [1.0, 0.0, 0.0]
        pelvisT.TransformVector(xaxis, xaxis)
        xaxis = np.array(xaxis)
        zaxis = np.array([0.0, 0.0, 1.0])
        yaxis = np.cross(zaxis, xaxis)
        yaxis /= np.linalg.norm(yaxis)
        xaxis = np.cross(yaxis, zaxis)

        stancePosition = (np.array(t2.GetPosition()) + np.array(t1.GetPosition())) / 2.0

        footHeight = 0.0811

        t = transformUtils.getTransformFromAxes(xaxis, yaxis, zaxis)
        t.PostMultiply()
        t.Translate(stancePosition)
        t.Translate([0.0, 0.0, -footHeight])

        return t

    def computeRobotStanceFrame(self, objectTransform, relativeStanceTransform):
        '''
        Given a robot model, determine the height of the ground
        using an XY and Yaw standoff, combined to determine the relative 6DOF standoff
        For a grasp or approach stance
        '''

        groundFrame = self.computeGroundFrame(self.robotModel)
        groundHeight = groundFrame.GetPosition()[2]

        graspPosition = np.array(objectTransform.GetPosition())
        graspYAxis = [0.0, 1.0, 0.0]
        graspZAxis = [0.0, 0.0, 1.0]
        objectTransform.TransformVector(graspYAxis, graspYAxis)
        objectTransform.TransformVector(graspZAxis, graspZAxis)

        xaxis = graspYAxis
        #xaxis = graspZAxis
        zaxis = [0, 0, 1]
        yaxis = np.cross(zaxis, xaxis)
        yaxis /= np.linalg.norm(yaxis)
        xaxis = np.cross(yaxis, zaxis)

        graspGroundTransform = transformUtils.getTransformFromAxes(xaxis, yaxis, zaxis)
        graspGroundTransform.PostMultiply()
        graspGroundTransform.Translate(graspPosition[0], graspPosition[1], groundHeight)

        robotStance = transformUtils.copyFrame( relativeStanceTransform )
        robotStance.Concatenate(graspGroundTransform)

        return robotStance


    def updatePointcloudSnapshot(self):

        if (self.useLidar is True):
            return vis.updatePolyData(segmentation.getCurrentRevolutionData(), 'pointcloud snapshot', parent='segmentation')
        else:
            return vis.updatePolyData(segmentation.getDisparityPointCloud(4), 'pointcloud snapshot', parent='segmentation')


    ### Valve Focused Functions ######################################################################
    def segmentValveWallAuto(self, expectedValveRadius=0.195, mode='both'):
        om.removeFromObjectModel(om.findObjectByName('affordances'))

        self.grabPointcloudSnapshot()

        self.affordanceFitFunction(expectedValveRadius=expectedValveRadius, mode=mode)


    def onImageViewDoubleClick(self, displayPoint, modifiers, imageView):

        if modifiers != QtCore.Qt.ControlModifier:
            return

        imagePixel = imageView.getImagePixel(displayPoint)
        cameraPos, ray = imageView.getWorldPositionAndRay(imagePixel)

        polyData = self.updatePointcloudSnapshot().polyData
        pickPoint = segmentation.extractPointsAlongClickRay(cameraPos, ray, polyData)

        om.removeFromObjectModel(om.findObjectByName('valve'))
        segmentation.segmentValveByBoundingBox(polyData, pickPoint)
        self.findAffordance()


    def computeValveStanceFrame(self):
        objectTransform = transformUtils.copyFrame( self.clenchFrame.transform )
        self.relativeStanceTransform = transformUtils.copyFrame( transformUtils.frameFromPositionAndRPY( self.relativeStanceXYZ , self.relativeStanceRPY ) )
        #robotStance = self.computeRobotStanceFrame(objectTransform, self.relativeStanceTransform)
        robotStance = self.getStanceFrameCoaxial()
        self.stanceFrame = vis.updateFrame(robotStance, 'valve grasp stance', parent=self.valveAffordance, visible=False, scale=0.2)
        self.stanceFrame.addToView(app.getDRCView())

    def spawnValveFrame(self, robotModel, height):

        position = [0.7, 0.22, height]
        rpy = [180, -90, 0]
        t = transformUtils.frameFromPositionAndRPY(position, rpy)
        t.Concatenate(self.computeGroundFrame(robotModel))
        return t

    def spawnValveAffordance(self):
        self.graspingObject = 'valve'
        spawn_height = 1.2192 # 4ft
        radius = 0.19558 # nominal initial value. 7.7in radius metal valve
        zwidth = 0.02
        thickness = 0.0254 # i think zwidth and thickness are duplicates

        valveFrame = self.spawnValveFrame(self.robotModel, spawn_height)
        folder = om.getOrCreateContainer('affordances')
        z = DebugData()
        #z.addLine ( np.array([0, 0, -thickness]) , np.array([0, 0, thickness]), radius=radius)
        z.addTorus( radius, 0.127 )
        z.addLine(np.array([0,0,0]), np.array([radius-zwidth,0,0]), radius=zwidth) # main bar
        valveMesh = z.getPolyData()

        self.valveAffordance = vis.showPolyData(valveMesh, 'valve', color=[0.0, 1.0, 0.0], cls=affordanceitems.FrameAffordanceItem, parent=folder, alpha=0.3)
        self.valveAffordance.actor.SetUserTransform(valveFrame)
        self.valveFrame = vis.showFrame(valveFrame, 'valve frame', parent=self.valveAffordance, visible=False, scale=0.2)
        self.valveFrame = self.valveFrame.transform

        params = dict(radius=radius, length=zwidth, xwidth=radius, ywidth=radius, zwidth=zwidth,
                      otdf_type='steering_cyl', friendly_name='valve')
        self.valveAffordance.setAffordanceParams(params)
        self.valveAffordance.updateParamsFromActorTransform()

    def spawnValveLeverAffordance(self):
        self.graspingObject = 'lever'
        spawn_height = 1.06 # 3.5ft
        pipe_radius = 0.01
        lever_length = 0.33

        valveFrame = self.spawnValveFrame(self.robotModel, spawn_height)
        folder = om.getOrCreateContainer('affordances')
        z = DebugData()
        z.addLine([0,0,0], [ lever_length , 0, 0], radius=pipe_radius)
        valveMesh = z.getPolyData()

        self.valveAffordance = vis.showPolyData(valveMesh, 'lever', color=[0.0, 1.0, 0.0], cls=affordanceitems.FrameAffordanceItem, parent=folder, alpha=0.3)
        self.valveAffordance.actor.SetUserTransform(valveFrame)
        self.valveFrame = vis.showFrame(valveFrame, 'lever frame', parent=self.valveAffordance, visible=False, scale=0.2)

        otdfType = 'lever_valve'
        params = dict( radius=pipe_radius, length=lever_length, friendly_name=otdfType, otdf_type=otdfType)
        self.valveAffordance.setAffordanceParams(params)
        self.valveAffordance.updateParamsFromActorTransform()

    def findAffordance(self):
        self.setupAffordanceParams()
        if (self.graspingObject is 'valve'):
            self.findValveAffordance()
        else:
            self.findValveLeverAffordance()

    def setupAffordanceParams(self):
        self.setupStance()

        self.relativeStanceXYZ = self.relativeStanceXYZInitial
        self.relativeStanceRPY = self.relativeStanceRPYInitial
        self.nextScribeAngle = self.nextScribeAngleInitial

        # mirror stance and rotation direction for right hand:
        if (self.graspingHand is 'right'):
            self.relativeStanceXYZ[1] = -self.relativeStanceXYZ[1]
            self.relativeStanceRPY[2] = -self.relativeStanceRPY[2]
            self.nextScribeAngle = -self.nextScribeAngle

    def updateTouchAngleVisualization(self, angle):
        if self.valveAffordance:

            obj = om.findObjectByName('valve touch angle')

            t = vtk.vtkTransform()
            t.PostMultiply()
            t.RotateX(angle)
            t.Concatenate(self.valveAffordance.getChildFrame().transform)

            if not obj:
                pose = transformUtils.poseFromTransform(t)
                length = self.valveAffordance.getProperty('Radius')*2
                desc = dict(classname='CylinderAffordanceItem', Name='valve touch angle',
                        uuid=segmentation.newUUID(), pose=pose, Radius=0.01, Length=length, Color=[1.0, 1.0, 0.0])

                import affordancepanel
                obj = affordancepanel.panel.affordanceFromDescription(desc)

            obj.getChildFrame().copyFrame(t)


    def findValveAffordance(self):
        self.valveAffordance = om.findObjectByName('valve')
        if self.valveAffordance is None:
            return

        valveFrame = self.valveAffordance.getChildFrame()

        t = vtk.vtkTransform()
        t.PostMultiply()
        t.RotateX(180)
        t.RotateY(-90)
        t.Concatenate(valveFrame.transform)
        self.valveFrame = t

        self.scribeRadius = self.valveAffordance.params.get('radius')# for pointer this was (radius - 0.06)

        self.computeClenchFrame()
        self.computeValveStanceFrame()

        self.frameSync = vis.FrameSync()
        self.frameSync.addFrame(valveFrame)
        self.frameSync.addFrame(self.clenchFrame, ignoreIncoming=True)
        self.frameSync.addFrame(self.stanceFrame, ignoreIncoming=True)

        # make an affordance to visualize the scribe angle


    def findValveLeverAffordance(self):

        self.valveAffordance = om.findObjectByName('lever')
        self.valveFrame = om.findObjectByName('lever frame')

        # length of lever is equivalent to radius of valve
        self.scribeRadius = self.valveAffordance.params.get('length') - 0.10

        self.computeClenchFrame()
        self.computeValveStanceFrame()

        self.frameSync = vis.FrameSync()
        self.frameSync.addFrame(self.valveFrame)
        self.frameSync.addFrame(self.clenchFrame)
        self.frameSync.addFrame(self.stanceFrame)

    def computeClenchFrame(self):
        t = transformUtils.frameFromPositionAndRPY(self.clenchFrameXYZ, self.clenchFrameRPY)
        t_copy = transformUtils.copyFrame(t)
        t_copy.Concatenate(self.valveFrame)
        self.clenchFrame = vis.updateFrame(t_copy, 'valve clench frame', parent=self.valveAffordance, visible=False, scale=0.2)
        self.clenchFrame.addToView(app.getDRCView())

    def computeTouchFrame(self, touchValve):
        if touchValve:
            faceDepth = self.touchDepth
        else:
            faceDepth = self.reachDepth

        assert self.valveAffordance

        t = transformUtils.frameFromPositionAndRPY([0,faceDepth,0], [0,0,0])

        position = [ self.scribeRadius*math.cos( math.radians( self.nextScribeAngle )) ,  self.scribeRadius*math.sin( math.radians( self.nextScribeAngle ))  , 0]
        # roll angle governs how much the palm points along towards the rotation axis
        # yaw ensures thumb faces the axis
        if (self.graspingObject is 'valve'):
            # valve, left and right
            rpy = [90+self.palmInAngle, 0, (270+self.nextScribeAngle)]
        else:
            if (self.graspingHand is 'left'): # lever left
                rpy = [90, 0, (180+self.nextScribeAngle)]
            else:
                rpy = [90, 0, self.nextScribeAngle]

        t2 = transformUtils.frameFromPositionAndRPY(position, rpy)
        t.Concatenate(t2)
        self.faceTransformLocal = transformUtils.copyFrame(t)

        t.Concatenate(self.valveFrame)
        self.faceFrameDesired = vis.showFrame(t, 'face frame desired', parent=self.valveAffordance, visible=False, scale=0.2)

    def drawFacePath(self):

        path = DebugData()
        for i in range(1,len(self.facePath)):
          p0 = self.facePath[i-1].GetPosition()
          p1 = self.facePath[i].GetPosition()
          path.addLine ( np.array( p0 ) , np.array(  p1 ), radius= 0.005)

        pathMesh = path.getPolyData()
        self.pointerTipLinePath = vis.showPolyData(pathMesh, 'face frame desired path', color=[0.0, 0.3, 1.0], parent=self.valveAffordance, alpha=0.6)
        self.pointerTipLinePath.actor.SetUserTransform(self.valveFrame)


    ### End Valve Focused Functions ###############################################################
    ### Planning Functions ###############################################################

    # These are operational conveniences:
    def planFootstepsToStance(self):
        self.planFootsteps(self.stanceFrame.transform)

    def planFootsteps(self, goalFrame):
        startPose = self.getPlanningStartPose()
        request = self.footstepPlanner.constructFootstepPlanRequest(startPose, goalFrame)
        self.footstepPlan = self.footstepPlanner.sendFootstepPlanRequest(request, waitForResponse=True)

    def planWalking(self):
        startPose = self.getPlanningStartPose()
        walkingPlan = self.footstepPlanner.sendWalkingPlanRequest(self.footstepPlan, startPose, waitForResponse=True)
        self.addPlan(walkingPlan)

    def planPreGrasp(self):
        startPose = self.getPlanningStartPose()
        endPose = self.ikPlanner.getMergedPostureFromDatabase(startPose, 'General', 'arm up pregrasp', side=self.graspingHand)
        newPlan = self.ikPlanner.computePostureGoal(startPose, endPose)
        self.addPlan(newPlan)

    def planNominal(self):
        startPose = self.getPlanningStartPose()
        endPose, info = self.ikPlanner.computeStandPose(startPose)
        endPose = self.ikPlanner.getMergedPostureFromDatabase(endPose, 'General', 'safe nominal')
        newPlan = self.ikPlanner.computePostureGoal(startPose, endPose)
        self.addPlan(newPlan)

    def coaxialGetPose(self, reachDepth, lockFeet=True, lockBack=True, preTurn=True, startPose=None):
        _, _, zaxis = transformUtils.getAxesFromTransform(self.valveFrame)
        yawDesired = np.arctan2(zaxis[1], zaxis[0])
        if self.graspingHand == 'left':
            larmName = 'l_larm'
            mwxJoint = 'l_arm_mwx'
            yJoints = ['l_arm_uwy']
            if preTurn:
                yJointLowerBound = [0.01]
                yJointUpperBound = [0.01]
            else:
                yJointLowerBound = [np.pi-0.01]
                yJointUpperBound = [np.pi-0.01]

        else:
            larmName = 'r_larm'
            mwxJoint = 'r_arm_mwx'
            yJoints = ['r_arm_uwy']
            if preTurn:
                yJointLowerBound = [np.pi-0.01]
                yJointUpperBound = [np.pi-0.01]
            else:
                yJointLowerBound = [0.01]
                yJointUpperBound = [0.01]

        if startPose is None:
            startPose = self.getPlanningStartPose()


        nominalPose, _ = self.ikPlanner.computeNominalPose(startPose)
        nominalPose[5] = yawDesired
        nominalPoseName = 'qNomAtRobot'
        self.ikPlanner.addPose(nominalPose, nominalPoseName)

        startPoseName = 'Start'
        #startPose[5] = yawDesired
        self.ikPlanner.addPose(startPose, startPoseName)
        self.ikPlanner.reachingSide = self.graspingHand

        constraints = []
        constraints.append(self.ikPlanner.createLockedArmPostureConstraint(startPoseName))

        if lockFeet:
            constraints.append(self.ikPlanner.createZMovingBasePostureConstraint(startPoseName))
            constraints.extend(self.ikPlanner.createFixedFootConstraints(startPoseName))
        else:
            constraints.append(self.ikPlanner.createXYZMovingBasePostureConstraint(nominalPoseName))
            constraints.extend(self.ikPlanner.createSlidingFootConstraints(startPose))

        if lockBack:
            constraints.append(self.ikPlanner.createLockedBackPostureConstraint(startPoseName))
        else:
            constraints.append(self.ikPlanner.createMovingBackLimitedPostureConstraint())

        constraints.append(self.ikPlanner.createKneePostureConstraint([0.7, 2.5]))

        tol = 0.01
        if reachDepth >= 0:
            elbowOnValveAxisConstraint = ik.PositionConstraint(linkName=larmName,
                                                               referenceFrame=self.clenchFrame.transform)
            elbowOnValveAxisConstraint.lowerBound = [tol, -np.inf, tol]
            elbowOnValveAxisConstraint.upperBound = [tol, np.inf, tol]
            constraints.append(elbowOnValveAxisConstraint)

            p = ik.PostureConstraint()
            p.joints = [mwxJoint]
            p.jointsLowerBound = [0]
            p.jointsUpperBound = [0]
            constraints.append(p)

        constraints.append(self.ikPlanner.createQuasiStaticConstraint())

        constraints.append(self.ikPlanner.createGazeGraspConstraint(self.graspingHand, self.clenchFrame, coneThresholdDegrees=4))

        p = ik.PostureConstraint()
        p.joints = yJoints
        p.jointsLowerBound = yJointLowerBound
        p.jointsUpperBound = yJointUpperBound
        constraints.append(p)

        t = transformUtils.frameFromPositionAndRPY([0,reachDepth,0], [0,0,0])
        t.Concatenate(self.clenchFrame.transform)

        constraintSet = self.ikPlanner.newReachGoal(startPoseName, self.graspingHand, t, constraints, lockOrient=False)
        constraintSet.constraints[-1].lowerBound = np.array([-tol, 0, -tol])
        constraintSet.constraints[-1].upperBound = np.array([tol, 0, tol])

        constraintSet.nominalPoseName = nominalPoseName;
        constraintSet.startPoseName = startPoseName;
        return constraintSet.runIk()


    def coaxialPlan(self, reachDepth, **kwargs):
        startPose = self.getPlanningStartPose()
        touchPose, info = self.coaxialGetPose(reachDepth, **kwargs)
        self.ikPlanner.computePostureGoal(startPose, touchPose)

    def coaxialPlanPreTouch(self, **kwargs):
        self.coaxialPlan(-0.1, **kwargs)

    def coaxialPlanTouch(self, **kwargs):
        self.coaxialPlan(0.05, **kwargs)

    def coaxialPlanTurn(self, **kwargs):
        self.coaxialPlan(0.05, preTurn=False, **kwargs)

    def coaxialPlanRetract(self, **kwargs):
        self.coaxialPlan(-0.1, preTurn=False, **kwargs)

    def getStanceFrameCoaxial(self):
        stancePose, info = self.coaxialGetPose(0.05, lockFeet=False)
        stanceRobotModel = self.ikPlanner.getRobotModelAtPose(stancePose)
        return self.footstepPlanner.getFeetMidPoint(stanceRobotModel)



        #p = ik.PostureConstraint()
        #if self.graspingHand is 'left':
            #p.joints = ['l_arm_uwy', 'l_arm_mwx']


        #constraints.append(reachingArmPostureConstraint)
        #constraints.extend(self.ikPlanner.createSlidingFootConstraints(startPose))
        #return self.ikPlanner.newReachGoal(startPoseName, self.graspingHand, self.clenchFrame, constraints, lockOrient=False)

    def getPlannedTouchAngleCoaxial(self):
        # when the pose is computed in getStanceFrameCoaxial, we could
        # store the turn angle. This method just returns the stored value.
        return 0.0

    def setDesiredTouchAngleCoaxial(self, angle):
        # this is the turn angle that the user wants.
        # this should be close to the planned touch angle, but the user may
        # adjust that value to avoid hitting the spokes.

        self.updateTouchAngleVisualization(angle)

    def planReach(self):
        self.computeTouchFrame(False) # 0 = not in contact
        self.computeTouchPlan()

    def planGrasp(self):
        self.computeTouchFrame(True)
        self.computeTouchPlan()

    def computeTouchPlan(self):
        # new full 6 dof constraint:
        startPose = self.getPlanningStartPose()

        nominalPose, _ = self.ikPlanner.computeNominalPose(startPose)
        self.ikPlanner.addPose(nominalPose, 'nominal_at_stance')
        reachNominalPose = self.ikPlanner.getMergedPostureFromDatabase(nominalPose, 'General', 'arm up pregrasp', side=self.graspingHand)
        self.ikPlanner.addPose(reachNominalPose, 'reach_nominal_at_stance')

        self.constraintSet = self.ikPlanner.planEndEffectorGoal(startPose, self.graspingHand, self.faceFrameDesired, lockBase=self.lockBase, lockBack=self.lockBack)

        self.constraintSet.nominalPoseName = 'reach_nominal_at_stance'
        self.constraintSet.seedPoseName = 'reach_nominal_at_stance'

        endPose, info = self.constraintSet.runIk()

        self.ikPlanner.ikServer.maxDegreesPerSecond = self.speedLow
        self.planTrajectory()
        self.ikPlanner.ikServer.maxDegreesPerSecond = self.speedHigh

    def planValveTurn(self, turnDegrees=360):
        # 10deg per sample
        numberOfSamples = int(round(turnDegrees/10.0))

        self.facePath = []
        self.resetTurnPath()

        degreeStep = float(turnDegrees) / numberOfSamples
        tipMode = False if self.scribeInAir else True

        self.computeTouchFrame(tipMode)
        self.initConstraintSet(self.faceFrameDesired)
        self.facePath.append(self.faceTransformLocal)

        for i in xrange(numberOfSamples):
            self.nextScribeAngle += self.scribeDirection*degreeStep
            self.computeTouchFrame(tipMode)
            self.appendPositionOrientationConstraintForTargetFrame(self.faceFrameDesired, i+1)
            self.facePath.append(self.faceTransformLocal)

        self.drawFacePath()
        self.ikPlanner.ikServer.maxDegreesPerSecond = self.speedLow
        self.planTrajectory()
        self.ikPlanner.ikServer.maxDegreesPerSecond = self.speedHigh

    def initConstraintSet(self, goalFrame):

        # create constraint set
        startPose = self.getPlanningStartPose()
        startPoseName = 'gaze_plan_start'
        endPoseName = 'gaze_plan_end'
        self.ikPlanner.addPose(startPose, startPoseName)
        self.ikPlanner.addPose(startPose, endPoseName)
        self.constraintSet = ikplanner.ConstraintSet(self.ikPlanner, [], startPoseName, endPoseName)
        self.constraintSet.endPose = startPose

        # add body constraints
        bodyConstraints = self.ikPlanner.createMovingBodyConstraints(startPoseName, lockBase=self.lockBase, lockBack=self.lockBack, lockLeftArm=self.graspingHand=='right', lockRightArm=self.graspingHand=='left')
        self.constraintSet.constraints.extend(bodyConstraints)

        self.constraintSet.constraints.append(self.ikPlanner.createKneePostureConstraint([0.6, 2.5]))

        # add gaze constraint - TODO: this gaze constraint shouldn't be necessary, fix
        self.graspToHandLinkFrame = self.ikPlanner.newGraspToHandFrame(self.graspingHand)
        #gazeConstraint = self.ikPlanner.createGazeGraspConstraint(self.graspingHand, goalFrame, self.graspToHandLinkFrame, coneThresholdDegrees=5.0)
        #self.constraintSet.constraints.insert(0, gazeConstraint)

    def appendDistanceConstraint(self):

        # add point to point distance constraint
        c = ikplanner.ik.PointToPointDistanceConstraint()
        c.bodyNameA = self.ikPlanner.getHandLink(self.graspingHand)
        c.bodyNameB = 'world'
        c.pointInBodyA = self.graspToHandLinkFrame
        c.pointInBodyB = self.valveFrame
        c.lowerBound = [self.scribeRadius]
        c.upperBound = [self.scribeRadius]
        self.constraintSet.constraints.insert(0, c)

    def appendPositionOrientationConstraintForTargetFrame(self, goalFrame, t):
        positionConstraint, orientationConstraint = self.ikPlanner.createPositionOrientationGraspConstraints(self.graspingHand, goalFrame, self.graspToHandLinkFrame)
        positionConstraint.tspan = [t, t]
        orientationConstraint.tspan = [t, t]
        self.constraintSet.constraints.append(positionConstraint)
        self.constraintSet.constraints.append(orientationConstraint)

    def planTrajectory(self):
        self.ikPlanner.ikServer.usePointwise = False
        plan = self.constraintSet.runIkTraj()
        self.addPlan(plan)


    ########## Glue Functions ####################################
    def moveRobotToStanceFrame(self, frame):
        self.sensorJointController.setPose('q_nom')
        stancePosition = frame.GetPosition()
        stanceOrientation = frame.GetOrientation()

        q = self.sensorJointController.q.copy()
        q[:2] = [stancePosition[0], stancePosition[1]]
        q[5] = math.radians(stanceOrientation[2])
        self.sensorJointController.setPose('EST_ROBOT_STATE', q)

    def getHandDriver(self, side):
        assert side in ('left', 'right')
        return self.lhandDriver if side == 'left' else self.rhandDriver

    def openHand(self,side):
        #self.handDriver(side).sendOpen()
        self.getHandDriver(side).sendCustom(0.0, 100.0, 100.0, 0)

    def openPinch(self,side):
        self.getHandDriver(side).sendCustom(20.0, 100.0, 100.0, 1)

    def closeHand(self, side):
        #self.handDriver(side).sendClose(60)
        self.getHandDriver(side).sendCustom(100.0, 100.0, 100.0, 0)

    def sendNeckPitchLookDown(self):
        self.multisenseDriver.setNeckPitch(40)

    def sendNeckPitchLookForward(self):
        self.multisenseDriver.setNeckPitch(15)

    def waitForAtlasBehaviorAsync(self, behaviorName):
        assert behaviorName in self.atlasDriver.getBehaviorMap().values()
        while self.atlasDriver.getCurrentBehaviorName() != behaviorName:
            yield

    def printAsync(self, s):
        yield
        print s

    def optionalUserPrompt(self, message):
        if not self.optionalUserPromptEnabled:
            return

        yield
        result = raw_input(message)
        if result != 'y':
            raise Exception('user abort.')

    def requiredUserPrompt(self, message):
        if not self.requiredUserPromptEnabled:
            return

        yield
        result = raw_input(message)
        if result != 'y':
            raise Exception('user abort.')

    def delay(self, delayTimeInSeconds):
        yield
        t = SimpleTimer()
        while t.elapsed() < delayTimeInSeconds:
            yield

    def waitForCleanLidarSweepAsync(self):
        currentRevolution = self.multisenseDriver.displayedRevolution
        desiredRevolution = currentRevolution + 2
        while self.multisenseDriver.displayedRevolution < desiredRevolution:
            yield

    def getEstimatedRobotStatePose(self):
        return self.sensorJointController.getPose('EST_ROBOT_STATE')

    def getPlanningStartPose(self):
        if self.planFromCurrentRobotState:
            return self.getEstimatedRobotStatePose()
        else:
            if self.plans:
                return robotstate.convertStateMessageToDrakePose(self.plans[-1].plan[-1])
            else:
                return self.getEstimatedRobotStatePose()

    def cleanupFootstepPlans(self):
        om.removeFromObjectModel(om.findObjectByName('walking goal'))
        om.removeFromObjectModel(om.findObjectByName('footstep plan'))
        self.footstepPlan = None

    def playSequenceNominal(self):
        assert None not in self.plans
        self.planPlaybackFunction(self.plans)

    def commitManipPlan(self):
        self.manipPlanner.commitManipPlan(self.plans[-1])

    def commitFootstepPlan(self):
        self.footstepPlanner.commitFootstepPlan(self.footstepPlan)

    def waitForPlanExecution(self):
        while self.atlasDriver.getControllerStatus() != 'manipulating':
            yield
        while self.atlasDriver.getControllerStatus() == 'manipulating':
            yield

    def waitForWalkExecution(self):
        while self.atlasDriver.getControllerStatus() != 'walking':
            yield
        while self.atlasDriver.getControllerStatus() == 'walking':
            yield

    def waitForPlanAnimation(self, plan):
        planElapsedTime = planplayback.PlanPlayback.getPlanElapsedTime(plan)
        print 'waiting for plan animation:', planElapsedTime
        return self.delay(planElapsedTime)

    def animateLastPlan(self):
        plan = self.plans[-1]
        if self.visOnly:
            return self.waitForPlanAnimation(plan)
        else:
            self.commitManipPlan()
            return self.waitForPlanExecution()

    ######### Nominal Plans and Execution  #################################################################
    def planSequence(self):

        self.cleanupFootstepPlans()
        self.resetTurnPath()

        self.planFromCurrentRobotState = False
        self.findAffordance()

        self.plans = []

        # Approach valve:
        if self.useFootstepPlanner:
            self.planFootstepsToStance()
            self.planWalking()
        else:
            self.moveRobotToStanceFrame(self.stanceFrame.transform )

        # Reach and Turn:
        self.planPreGrasp()
        self.planReach()
        self.planGrasp()
        self.planValveTurn(self.turnAngle)

        # Dereach and Stand
        self.planReach()
        self.planPreGrasp()
        self.planNominal()
        self.playSequenceNominal()

    def autonomousTest(self, msg):
        print "Got the autonomousTest message, executing valve test sequence"
        q = self.autonomousExecute()
        q.start()

    def sendAutonmousTestDone(self):
        msg = lcmdrc.utime_t()
        msg.utime = getUtime()
        lcmUtils.publish('AUTONOMOUS_TEST_VALVE_DONE', msg)


    def autonomousExecute(self):

        self.planFromCurrentRobotState = True
        self.visOnly = False
        self.nextScribeAngle = 45
        self.turnAngle=70
        self.graspingHand='right'

        taskQueue = AsyncTaskQueue()
        taskQueue.addTask(self.resetTurnPath)

        # Approach valve:
        taskQueue.addTask(self.waitForCleanLidarSweepAsync)
        taskQueue.addTask( functools.partial(self.segmentValveWallAuto, 0.23, self.graspingObject) )
        taskQueue.addTask(self.optionalUserPrompt('Accept valve fit, continue? y/n: '))
        taskQueue.addTask(self.findAffordance)

        taskQueue.addTask(self.printAsync('Plan and execute walking'))
        taskQueue.addTask(self.planFootstepsToStance)
        taskQueue.addTask(self.optionalUserPrompt('Send footstep plan. continue? y/n: '))
        taskQueue.addTask(self.commitFootstepPlan)
        taskQueue.addTask(self.waitForWalkExecution)

        # Fit the Valve:
        taskQueue.addTask(self.printAsync('Wait for sweep'))
        taskQueue.addTask(self.waitForCleanLidarSweepAsync)
        taskQueue.addTask( functools.partial(self.segmentValveWallAuto, 0.23, self.graspingObject) )
        taskQueue.addTask(self.optionalUserPrompt('Accept valve re-fit, continue? y/n: '))
        taskQueue.addTask(self.findAffordance)

        # Move arm to pregrasp:
        taskQueue.addTask(self.printAsync('Pre grasp'))
        taskQueue.addTask(self.planPreGrasp)
        taskQueue.addTask(self.optionalUserPrompt('Continue? y/n: '))
        taskQueue.addTask(self.animateLastPlan)

        taskQueue.addTask(self.printAsync('Turn 1'))
        taskQueue = self.addAutomousValveTurn(taskQueue, self.nextScribeAngle)
        taskQueue.addTask(self.printAsync('Turn 2'))
        taskQueue = self.addAutomousValveTurn(taskQueue, self.nextScribeAngle)
        taskQueue.addTask(self.printAsync('Turn 3'))
        taskQueue = self.addAutomousValveTurn(taskQueue, self.nextScribeAngle)
        taskQueue.addTask(self.printAsync('done!'))

        taskQueue.addTask(self.sendAutonmousTestDone)

        return taskQueue

    def autonomousExecuteTurn(self):
        '''
        Turn a valve by the turnAngle and then retract
        As initial conditions: assumes robot has hand in reach or pregrasp position
        '''

        self.planFromCurrentRobotState = True
        self.visOnly = False
        self.graspingHand='left'
        self.scribeDirection = 1

        taskQueue = AsyncTaskQueue()
        taskQueue.addTask(self.resetTurnPath)

        taskQueue.addTask(self.printAsync('Turn 1'))
        taskQueue = self.addAutomousValveTurn(taskQueue, self.nextScribeAngle)
        taskQueue.addTask(self.printAsync('done!'))

        return taskQueue


    def addAutomousValveTurn(self,taskQueue, nextScribeAngle):
        taskQueue.addTask(functools.partial( self.setNextScribeAngle, nextScribeAngle))
        taskQueue.addTask(self.printAsync('Reach'))
        taskQueue.addTask(self.planReach)
        taskQueue.addTask(self.optionalUserPrompt('Continue? y/n: '))
        taskQueue.addTask(self.animateLastPlan)

        taskQueue.addTask(self.printAsync('Reach'))
        taskQueue.addTask(self.planGrasp)
        taskQueue.addTask(self.optionalUserPrompt('Continue? y/n: '))
        taskQueue.addTask(self.animateLastPlan)
        taskQueue.addTask(functools.partial(self.closeHand,self.graspingHand))

        taskQueue.addTask(self.printAsync('Turn'))
        taskQueue.addTask(functools.partial( self.planValveTurn, self.turnAngle))
        taskQueue.addTask(self.optionalUserPrompt('Continue? y/n: '))
        taskQueue.addTask(self.animateLastPlan)
        taskQueue.addTask(functools.partial(self.openHand,self.graspingHand))

        taskQueue.addTask(self.printAsync('Dereach'))
        taskQueue.addTask(self.planReach)
        taskQueue.addTask(self.optionalUserPrompt('Continue? y/n: '))
        taskQueue.addTask(self.animateLastPlan)
        return taskQueue







import PythonQt
from PythonQt import QtCore, QtGui, QtUiTools

def addWidgetsToDict(widgets, d):

    for widget in widgets:
        if widget.objectName:
            d[str(widget.objectName)] = widget
        addWidgetsToDict(widget.children(), d)


class WidgetDict(object):

    def __init__(self, widgets):
        addWidgetsToDict(widgets, self.__dict__)


class ValveTaskPanel(object):

    def __init__(self, valveDemo):

        self.valveDemo = valveDemo

        self.valveDemo.reachDepth = -0.24
        self.valveDemo.speedLow = 20

        loader = QtUiTools.QUiLoader()
        uifile = QtCore.QFile(':/ui/ddValveTaskPanel.ui')
        assert uifile.open(uifile.ReadOnly)

        self.widget = loader.load(uifile)
        self.ui = WidgetDict(self.widget.children())

        self.ui.startButton.connect('clicked()', self.onStartClicked)
        self.ui.footstepsButton.connect('clicked()', self.valveDemo.planFootstepsToStance)
        self.ui.raiseArmButton.connect('clicked()', self.valveDemo.planPreGrasp)
        self.ui.reachButton.connect('clicked()', self.reach)
        self.ui.touchButton.connect('clicked()', self.grasp)
        self.ui.turnButton.connect('clicked()', self.turnValve)
        self.ui.fingersButton.connect('clicked()', self.setFingers)
        self.ui.retractButton.connect('clicked()', self.retract)
        self.ui.nominalButton.connect('clicked()', self.valveDemo.planNominal)

        l = QtGui.QVBoxLayout(self.ui.imageFrame)

        self.taskTree = tmw.TaskTree()
        self.ui.taskFrame.layout().insertWidget(0, self.taskTree.treeWidget)
        self.ui.taskFrame.layout().insertWidget(1, self.taskTree.propertiesPanel)
        self._setupParams()
        self._setupPropertiesPanel()
        self._syncProperties()

        self.taskTree.onAddTask(rt.PrintTask(name='print start message', message='starting valve demo'))
        self.taskTree.onAddTask(rt.CallbackTask(callback=self.valveDemo.findAffordance, name='find affordance'), copy=False)

    def onStartClicked(self):
      self.valveDemo.findAffordance()
      if self.valveDemo.valveAffordance is not None:
          print 'Valve Demo: Start - Ready to proceed'
      else:
          print 'Valve Demo: Start - VALVE AFFORDANCE NOT FOUND'

      # now get the planned turn angle and show it to the user
      self.params.setProperty('Touch angle (deg)', self.valveDemo.getPlannedTouchAngleCoaxial())

    def closeHand(self):
      self.valveDemo.closeHand(self.valveDemo.graspingHand)

    def setFingers(self):
      self.valveDemo.openPinch(self.valveDemo.graspingHand)

    def reach(self):
        self.valveDemo.coaxialPlanPreTouch()

    def grasp(self):
        self.valveDemo.coaxialPlanTouch()

    def turnValve(self):
        self.valveDemo.coaxialPlanTurn()

    def retract(self):
        self.valveDemo.coaxialPlanRetract()

    def _setupParams(self):
        self.params = om.ObjectModelItem('Valve Task Params')
        self.params.addProperty('Hand', 1, attributes=om.PropertyAttributes(enumNames=['Left', 'Right']))
        self.params.addProperty('Turn direction', 0, attributes=om.PropertyAttributes(enumNames=['Clockwise', 'Counter clockwise']))
        self.params.addProperty('Touch angle (deg)', 0)
        #self.params.addProperty('Turn amount (deg)', 60)
        self.params.properties.connectPropertyChanged(self.onPropertyChanged)

    def _setupPropertiesPanel(self):
        l = QtGui.QVBoxLayout(self.ui.propertyFrame)
        l.setMargin(0)
        self.propertiesPanel = PythonQt.dd.ddPropertiesPanel()
        self.propertiesPanel.setBrowserModeToWidget()
        l.addWidget(self.propertiesPanel)

        self.panelConnector = propertyset.PropertyPanelConnector(self.params.properties, self.propertiesPanel)


    def onPropertyChanged(self, propertySet, propertyName):
        self._syncProperties()

    def _syncProperties(self):

        self.valveDemo.planFromCurrentRobotState = True
        self.valveDemo.visOnly = False
        self.valveDemo.graspingHand = self.params.getPropertyEnumValue('Hand').lower()
        self.valveDemo.scribeDirection = 1 if self.params.getPropertyEnumValue('Turn direction') == 'Clockwise' else -1
        self.valveDemo.setDesiredTouchAngleCoaxial(self.params.getProperty('Touch angle (deg)'))
        #self.valveDemo.turnAngle = self.params.getProperty('Turn amount (deg)')


