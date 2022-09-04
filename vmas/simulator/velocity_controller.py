#  Copyright (c) 2022.
#  ProrokLab (https://www.proroklab.org/)
#  All rights reserved.
import torch
import numpy as np;
import vmas.simulator.core
import vmas.simulator.utils

"""
Implements PID controller for velocity targets found in agent.action.u.
Two forms of the PID controller are implemented: standard, and parallel. The controller takes 3 params, which
are interpreted differently based on the form.
> Standard form: ctrl_params=[gain, intg_ts, derv_ts]
                    intg_ts: rise time for integrator (err will be tolerated for this interval)
                    derv_ts: seek time for derivative (err is predicted over this interval)
                    These are specified in 1/dt scale (0.5 means 0.5/0.1==5sec)
> Parallel form: ctrl_params=[kP, kI, kD]
                    kI and kD have no simple physical meaning, but are related to standard form params.
                    intg_ts = kP/kI and kD/kP = derv_ts
"""
class VelocityController:
    def __init__(self, agent: vmas.simulator.core.Agent, dt: float, ctrl_params=[1, 0, 0], pid_form="standard"):
        self.agent = agent
        self.dt = dt
        # controller parameters: standard=[kP, intgTs ,dervTs], parallel=[kP, kI, kD]
        #    in parallel form, kI = kP/intgTs and kD = kP*dervTs
        self.ctrl_gain = ctrl_params[0];    # kP
        if pid_form == "standard":
            self.integralTs = ctrl_params[1];
            self.derivativeTs = ctrl_params[2];
        elif pid_form == "parallel":
            if ctrl_params[1] == 0:
                self.integralTs = 0.0;
            else:
                self.integralTs = self.ctrl_gain / ctrl_params[1];
            self.derivativeTs = ctrl_params[2] / self.ctrl_gain;
        else:
            raise Exception( "PID form is either standard or parallel." );
        
        # in either form:
        if self.integralTs == 0:
            self.use_integrator = False;
        else:
            self.use_integrator = True;
            # set windup limit to 50% of agent's max force
            fmax = self.agent.max_f if self.agent.max_f is not None else 2.0;
            self.integrator_windup_cutoff = 0.5 * fmax * self.integralTs/(self.dt * self.ctrl_gain);
        
        # containers for integral & derivative control
        self.accum_errs = 0.0;
        self.prev_err = 0.0;

        # do other initialisation bits
        self.reset();

    def reset(self):
        self.accum_errs = 0.0;
        self.prev_err = 0.0;
    
    def integralError(self, err):
        if not self.use_integrator:
            return 0;
        # fixed-length history (not recommended):
        ### if len( self.accum_errs ) > self.integrator_hist-1:
        ###    self.accum_errs.pop(0);
        ### self.accum_errs.append( err );
        ### return (1.0/self.integralTs) * torch.stack( self.accum_errs, dim=1 ).sum(dim=1) * self.dt;
        
        self.accum_errs += ( self.dt * err );
        self.accum_errs = vmas.simulator.utils.clamp_tensor(self.accum_errs, self.integrator_windup_cutoff);
        return (1.0/self.integralTs) * self.accum_errs;
    
    def rateError(self, err):
        e = self.derivativeTs * (err - self.prev_err)/self.dt;
        self.prev_err = err;
        return e;
            

    def process_force(self):
        des_vel = self.agent.action.u;
        cur_vel = self.agent.state.vel;

        # apply control
        err = des_vel - cur_vel;
        u = self.ctrl_gain * ( err + self.integralError(err) + self.rateError(err) );
        u = u * self.agent.mass;

        # Clamping force to limits
        if self.agent.max_f is not None:
            u = vmas.simulator.utils.clamp_with_norm(u, self.agent.max_f)
        if self.agent.f_range is not None:
            u = torch.clamp(u, -self.agent.f_range, self.agent.f_range)

        self.agent.action.u = u;
