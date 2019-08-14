"""
Copyright 2013 Steven Diamond

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import cvxpy.settings as s
import cvxpy.lin_ops.lin_op as lo
from cvxpy.constraints import (Equality, ExpCone, Inequality,
                               SOC, Zero, NonPos, PSD)
from cvxpy.expressions.variable import Variable
from cvxpy.problems.objective import Minimize
from cvxpy.reductions import InverseData, Solution
from cvxpy.reductions.cvx_attr2constr import convex_attributes
from cvxpy.reductions.matrix_stuffing import extract_mip_idx, MatrixStuffing
from cvxpy.reductions.utilities import (are_args_affine,
                                        group_constraints,
                                        lower_equality,
                                        lower_inequality)
from cvxpy.utilities.coeff_extractor import CoeffExtractor
import numpy as np
import scipy.sparse as sp


class ParamConeProg(object):
    """Represents a parameterized cone program

    minimize   c'x  + d
    subject to cone_constr1(A_1*x + b_1, ...)
               ...
               cone_constrK(A_i*x + b_i, ...)


    The constant offsets d and b are the last column of c and A.
    """
    def __init__(self, c, x, A,
                 variables,
                 var_id_to_col,
                 constraints,
                 parameters,
                 param_id_to_col):
        self.c = c
        self.x = x
        self.A = A
        self.constraints = constraints
        self.constr_size = sum([c.size for c in constraints])
        self.parameters = parameters
        self.param_id_to_col = param_id_to_col
        self.id_to_param = {p.id: p for p in self.parameters}
        self.total_param_size = sum([p.size for p in self.parameters])
        # TODO technically part of inverse data.
        self.variables = variables
        self.var_id_to_col = var_id_to_col
        self.id_to_var = {v.id: v for v in self.variables}

    def is_mixed_integer(self):
        return self.x.attributes['boolean'] or \
            self.x.attributes['integer']

    def apply_parameters(self, id_to_param_value=None):
        """Returns A, b after applying parameters (and reshaping).

        Args:
          id_to_param_value: (optional) dict mapping parameter ids to values
        """
        def param_value(idx):
            return (np.array(self.id_to_param[idx].value) if id_to_param_value
                    is None else id_to_param_value[idx])

        # Flatten parameters.
        param_vec = np.zeros(self.total_param_size + 1)
        # TODO handle parameters with structure.
        for param_id, col in self.param_id_to_col.items():
            if param_id == lo.CONSTANT_ID:
                param_vec[col] = 1
            else:
                value = param_value(param_id).flatten(order='F')
                param = self.id_to_param[param_id]
                param_vec[col:param.size+col] = value
        # New problem without parameters.
        c = (self.c@param_vec).flatten()
        # Need to cast to sparse matrix.
        param_vec = sp.csc_matrix(param_vec[:, None])
        var_size = self.x.size + 1
        A = (self.A@param_vec).reshape((self.A.shape[0]//var_size, var_size),
                                       order='F')
        A = A.tocsc()
        return c, A

    def apply_param_jac(self, delc, delA, delb, active_params=None):
        """Multiplies by Jacobian of parameter mapping.

        Assumes delA is sparse.
        """
        if active_params is None:
            active_params = {p.id for p in self.parameters}

        del_param_vec = delc@self.c[:-1]
        flatdelA = delA.reshape((np.prod(delA.shape), 1), order='F')
        delAb = sp.vstack([flatdelA, sp.csc_matrix(delb[:, None])])
        del_param_vec += np.squeeze((delAb.T @ self.A).A)
        del_param_vec = np.squeeze(del_param_vec)
        # Make dictionary of param id to delta.
        del_param_dict = {}
        for param_id, col in self.param_id_to_col.items():
            if param_id in active_params:
                param = self.id_to_param[param_id]
                delta = del_param_vec[col:param.size+col]
                del_param_dict[param_id] = np.reshape(delta, param.shape,
                                                      order='F')

        return del_param_dict

    def split_solution(self, sltn, active_vars=None):
        """Splits the solution into individual variables.
        """
        if active_vars is None:
            active_vars = {v.id for v in self.variables}
        # var id to solution.
        sltn_dict = {}
        for var_id, col in self.var_id_to_col.items():
            if var_id in active_vars:
                var = self.id_to_var[var_id]
                value = sltn[col:var.size+col]
                sltn_dict[var_id] = np.reshape(value, var.shape,
                                               order='F')

        return sltn_dict

    def split_adjoint(self, del_vars):
        """Adjoint of split_solution.
        """
        var_vec = np.zeros(self.x.size)
        for var_id, delta in del_vars.items():
            var = self.id_to_var[var_id]
            col = self.var_id_to_col[var_id]
            var_vec[col:var.size+col] = delta.flatten(order='F')

        return var_vec


class ConeMatrixStuffing(MatrixStuffing):
    """Construct matrices for linear cone problems.

    Linear cone problems are assumed to have a linear objective and cone
    constraints which may have zero or more arguments, all of which must be
    affine.
    """
    CONSTRAINTS = 'ordered_constraints'

    def accepts(self, problem):
        return (type(problem.objective) == Minimize
                and problem.objective.expr.is_affine()
                and not convex_attributes(problem.variables())
                and are_args_affine(problem.constraints))

    def stuffed_objective(self, problem, extractor):
        # Extract to c.T * x + r
        c = extractor.affine(problem.objective.expr)

        boolean, integer = extract_mip_idx(problem.variables())
        x = Variable(extractor.x_length, boolean=boolean, integer=integer)

        return c, x

    def apply(self, problem):
        inverse_data = InverseData(problem)
        # Form the constraints
        extractor = CoeffExtractor(inverse_data)
        c, x = self.stuffed_objective(problem, extractor)
        # Lower equality and inequality to Zero and NonPos.
        cons = []
        for con in problem.constraints:
            orig_con = con
            if isinstance(con, Equality):
                con = lower_equality(con)
            elif isinstance(con, Inequality):
                con = lower_inequality(con)
            elif isinstance(con, SOC) and con.axis == 1:
                con = SOC(con.args[0], con.args[1].T, axis=0,
                          constr_id=con.constr_id)
            cons.append(con)
            for dv_old, dv_new in zip(orig_con.dual_variables,
                                      con.dual_variables):
                inverse_data.dv_id_map[dv_new] = dv_old
        # Reorder constraints to Zero, NonPos, SOC, PSD, EXP.
        constr_map = group_constraints(cons)
        ordered_cons = constr_map[Zero] + constr_map[NonPos] + \
            constr_map[SOC] + constr_map[PSD] + constr_map[ExpCone]

        inverse_data.constraints = ordered_cons
        # Batch expressions together, then split apart.
        expr_list = [arg for c in ordered_cons for arg in c.args]
        A = extractor.affine(expr_list)

        # Map of old constraint id to new constraint id.
        inverse_data.minimize = type(problem.objective) == Minimize
        # TODO backing out of ParamConeProg,
        # doing a matrix interface instead.
        new_prob = ParamConeProg(c, x, A,
                                 problem.variables(),
                                 inverse_data.var_offsets,
                                 ordered_cons,
                                 problem.parameters(),
                                 inverse_data.param_id_map)
        return new_prob, inverse_data
