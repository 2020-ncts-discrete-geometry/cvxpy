"""
Copyright 2017 Robin Verschueren, 2017 Akshay Agrawal

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
from cvxpy.constraints import Equality, ExpCone, Inequality, SOC
from cvxpy.reductions import Reduction, Solution, InverseData
from cvxpy.reductions.utilities import lower_equality, lower_inequality
from cvxpy.utilities.coeff_extractor import CoeffExtractor
from cvxpy.atoms import reshape
from cvxpy import problems
from cvxpy.problems.objective import Minimize
import abc
import numpy as np


def extract_mip_idx(variables):
    """Coalesces bool, int indices for variables.

       The indexing scheme assumes that the variables will be coalesced into
       a single one-dimensional variable, with each variable being reshaped
       in Fortran order.
    """
    def ravel_multi_index(multi_index, x, vert_offset):
        """Ravel a multi-index and add a vertical offset to it.
        """
        ravel_idx = np.ravel_multi_index(multi_index, max(x.shape, (1,)), order='F')
        return [(vert_offset + idx,) for idx in ravel_idx]
    boolean_idx = []
    integer_idx = []
    vert_offset = 0
    for x in variables:
        if x.boolean_idx:
            multi_index = list(zip(*x.boolean_idx))
            boolean_idx += ravel_multi_index(multi_index, x, vert_offset)
        if x.integer_idx:
            multi_index = list(zip(*x.integer_idx))
            integer_idx += ravel_multi_index(multi_index, x, vert_offset)
        vert_offset += x.size
    return boolean_idx, integer_idx


class MatrixStuffing(Reduction):
    """TODO(akshayka): Document this class."""
    __metaclass__ = abc.ABCMeta

    def apply(self, problem):
        inverse_data = InverseData(problem)
        # Form the constraints
        extractor = CoeffExtractor(inverse_data)
        new_obj, new_var, r = self.stuffed_objective(problem, extractor)
        inverse_data.r = r
        # Lower equality and inequality to Zero and NonPos.
        cons = []
        for con in problem.constraints:
            if isinstance(con, Equality):
                con = lower_equality(con)
            elif isinstance(con, Inequality):
                con = lower_inequality(con)
            elif isinstance(con, SOC) and con.axis == 1:
                con = SOC(con.args[0], con.args[1].T, axis=0,
                          constr_id=con.constr_id)
            cons.append(con)

        # Batch expressions together, then split apart.
        expr_list = [arg for c in cons for arg in c.args]
        # TODO QPs go here for constraints. Need to cast into right dimensions.
        Afull, bfull = extractor.affine(expr_list)
        new_cons = []
        offset = 0
        for con in cons:
            arg_list = []
            for arg in con.args:
                A = Afull[offset:offset+arg.size, :]
                b = bfull[offset:offset+arg.size]
                arg_list.append(reshape(A*new_var + b, arg.shape))
                offset += arg.size
            new_cons.append(con.copy(arg_list))
            for dv_old, dv_new in zip(con.dual_variables,
                                      new_cons[-1].dual_variables):
                inverse_data.dv_id_map[dv_new] = dv_old

        # Map of old constraint id to new constraint id.
        inverse_data.minimize = type(problem.objective) == Minimize
        new_prob = problems.problem.Problem(Minimize(new_obj), new_cons)
        return new_prob, inverse_data

    def invert(self, solution, inverse_data):
        """Returns the solution to the original problem given the inverse_data."""
        var_map = inverse_data.var_offsets
        # Flip sign of opt val if maximize.
        opt_val = solution.opt_val
        if solution.status not in s.ERROR and not inverse_data.minimize:
            opt_val = -solution.opt_val

        primal_vars, dual_vars = {}, {}
        if solution.status not in s.SOLUTION_PRESENT:
            return Solution(solution.status, opt_val, primal_vars, dual_vars,
                            solution.attr)

        # TODO make a double dictionary full of matrices
        # to represent mapping from primal to primal and dual to dual.

        # Split vectorized variable into components.
        x_opt = list(solution.primal_vars.values())[0]
        for var_id, offset in var_map.items():
            shape = inverse_data.var_shapes[var_id]
            size = np.prod(shape, dtype=int)
            primal_vars[var_id] = np.reshape(x_opt[offset:offset+size], shape,
                                             order='F')

        # Remap dual variables if dual exists (problem is convex).
        if solution.dual_vars is not None:
            # Giant dual variable.
            dual_var = list(solution.dual_vars.values())[0]
            offset = 0
            for constr in inverse_data.constraints:
                for dv in constr.dual_variables:
                    dv_old = inverse_data.dv_id_map[dv]
                    dual_vars[dv_old] = np.reshape(
                        dual_var[offset:offset+dv.size],
                        dv.shape,
                        order='F'
                    )
                    offset += dv.size

        return Solution(solution.status, opt_val, primal_vars, dual_vars,
                        solution.attr)

    def stuffed_objective(self, problem, inverse_data):
        return NotImplementedError
