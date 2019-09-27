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


import contextlib

from cvxpy import settings as s
from cvxpy.expressions.leaf import Leaf
import cvxpy.lin_ops.lin_utils as lu
from cvxpy.utilities import performance_utils as perf


@contextlib.contextmanager
def treat_params_as_affine(expr):
    """Treats parameters as affine, not constants."""
    with perf.disable_caches():
        # caches are disabled: we don't want to accidentally cache dpp
        # curvature (e.g., is_convex() might be false under this scope but true
        # when a param-affine scope is not active)
        old_constant_flags = []
        for p in expr.parameters():
            old_constant_flags.append(p._is_constant)
            p._is_constant = False
        expr._check_is_constant(recompute=True)

        yield

        for p, flag in zip(expr.parameters(), old_constant_flags):
            p._is_constant = flag
        expr._check_is_constant(recompute=True)


def param_affine_scope_active(expr):
    """Returns True if a `treat_params_as_affine` scope is active. """
    return expr.parameters() and not expr.parameters()[0].is_constant()


class Parameter(Leaf):
    """Parameters in optimization problems.

    Parameters are constant expressions whose value may be specified
    after problem creation. The only way to modify a problem after its
    creation is through parameters. For example, you might choose to declare
    the hyper-parameters of a machine learning model to be Parameter objects;
    more generally, Parameters are useful for computing trade-off curves.
    """
    PARAM_COUNT = 0

    def __init__(self, shape=(), name=None, value=None, id=None, **kwargs):
        if id is None:
            self.id = lu.get_id()
        else:
            self.id = id
        if name is None:
            self._name = "%s%d" % (s.PARAM_PREFIX, self.id)
        else:
            self._name = name
        # Initialize with value if provided.
        self._value = None
        super(Parameter, self).__init__(shape, value, **kwargs)
        self._is_constant = True

    def get_data(self):
        """Returns info needed to reconstruct the expression besides the args.
        """
        return [self.shape, self._name, self.value, self.id, self.attributes]

    def name(self):
        return self._name

    def _check_is_constant(self, recompute=False):
        return self._is_constant

    # Getter and setter for parameter value.
    @property
    def value(self):
        """NumPy.ndarray or None: The numeric value of the parameter.
        """
        return self._value

    @value.setter
    def value(self, val):
        self._value = self._validate_value(val)

    @property
    def grad(self):
        """Gives the (sub/super)gradient of the expression w.r.t. each variable.

        Matrix expressions are vectorized, so the gradient is a matrix.

        Returns:
            A map of variable to SciPy CSC sparse matrix or None.
        """
        return {}

    def parameters(self):
        """Returns itself as a parameter.
        """
        return [self]

    def canonicalize(self):
        """Returns the graph implementation of the object.

        Returns:
            A tuple of (affine expression, [constraints]).
        """
        obj = lu.create_param(self.shape, self.id)
        return (obj, [])

    def __repr__(self):
        """String to recreate the object.
        """
        attr_str = self._get_attr_str()
        if len(attr_str) > 0:
            return "Parameter(%s%s)" % (self.shape, attr_str)
        else:
            return "Parameter(%s)" % (self.shape,)
