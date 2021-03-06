#!/usr/bin/env python
#-*- coding:utf-8 -*-
##
## lsu.py
##
##  Created on: Aug 29, 2018
##      Author: Miguel Neves
##      E-mail: neves@sat.inesc-id.pt
##

"""
    ===============
    List of classes
    ===============

    .. autosummary::
        :nosignatures:

        LSU

    ==================
    Module description
    ==================

    The module implements a prototype of the known *LSU/LSUS*, e.g. *linear
    (search) SAT-UNSAT*, algorithm for MaxSAT, e.g. see [1]_.  The
    implementation is improved by the use of the *iterative totalizer encoding*
    [2]_. The encoding is used in an incremental fashion, i.e. it is created
    once and reused as many times as the number of iterations the algorithm
    makes.

    .. [1] António Morgado, Federico Heras, Mark H. Liffiton, Jordi Planes,
        Joao Marques-Silva. *Iterative and core-guided MaxSAT solving: A
        survey and assessment*. Constraints 18(4). 2013. pp. 478-534

    .. [2] Ruben Martins, Saurabh Joshi, Vasco M. Manquinho, Inês Lynce.
        *Incremental Cardinality Constraints for MaxSAT*. CP 2014. pp. 531-548

    The implementation can be used as an executable (the list of available
    command-line options can be shown using ``lsu.py -h``) in the following
    way:

    ::

        $ xzcat formula.wcnf.xz
        p wcnf 3 6 4
        1 1 0
        1 2 0
        1 3 0
        4 -1 -2 0
        4 -1 -3 0
        4 -2 -3 0

        $ lsu.py -s glucose3 -m -v formula.wcnf.xz
        c formula: 3 vars, 3 hard, 3 soft
        o 2
        s OPTIMUM FOUND
        v -1 -2 3 0
        c oracle time: 0.0000

    Alternatively, the algorithm can be accessed and invoked through the
    standard ``import`` interface of Python, e.g.

    .. code-block:: python

        >>> from pysat.examples.lsu import LSU
        >>> from pysat.formula import WCNF
        >>>
        >>> wcnf = WCNF(from_file='formula.wcnf.xz')
        >>>
        >>> lsu = LSU(wcnf, verbose=0)
        >>> lsu.solve()  # set of hard clauses should be satisfiable
        True
        >>> print lsu.cost # cost of MaxSAT solution should be 2
        >>> 2
        >>> print lsu.model
        [-1, -2, 3]

    ==============
    Module details
    ==============
"""

#
#==============================================================================
from __future__ import print_function
import getopt
from pysat.card import ITotalizer
from pysat.formula import CNF, WCNF
from pysat.solvers import Solver
import os
import sys
import re


# TODO: support weighted MaxSAT
#==============================================================================
class LSU:
    """
        Linear SAT-UNSAT algorithm for MaxSAT [1]_. The algorithm can be seen
        as a series of satisfiability oracle calls refining an upper bound on
        the MaxSAT cost, followed by one unsatisfiability call, which stops the
        algorithm. The implementation encodes the sum of all selector literals
        using the *iterative totalizer encoding* [2]_. At every iteration, the
        upper bound on the cost is reduced and enforced by adding the
        corresponding unit size clause to the working formula. No clauses are
        removed during the execution of the algorithm. As a result, the SAT
        oracle is used incrementally.

        .. warning:: At this point, :class:`LSU` supports only
            **unweighted** problems.

        The constructor receives an input :class:`.WCNF` formula, a name of the
        SAT solver to use (see :class:`.SolverNames` for details), and an
        integer verbosity level.

        :param formula: input MaxSAT formula
        :param solver: name of SAT solver
        :param verbose: verbosity level

        :type formula: :class:`.WCNF`
        :type solver: str
        :type verbose: int
    """

    def __init__(self, formula, solver='g4', verbose=0):
        """
            Constructor.
        """

        self.verbose = verbose
        self.solver = solver
        self.formula = formula
        self.topv = formula.nv  # largest variable index
        self.sels = []          # soft clause selector variables
        self.tot = None         # totalizer encoder for the cardinality constraint
        self._init(formula)     # initiaize SAT oracle

    def _init(self, formula):
        """
            SAT oracle initialization. The method creates a new SAT oracle and
            feeds it with the formula's hard clauses. Afterwards, all soft
            clauses of the formula are augmented with selector literals and
            also added to the solver. The list of all introduced selectors is
            stored in variable ``self.sels``.

            :param formula: input MaxSAT formula
            :type formula: :class:`WCNF`
        """

        self.oracle = Solver(name=self.solver, bootstrap_with=formula.hard,
                incr=True, use_timer=True)

        for i, cl in enumerate(formula.soft):
            # TODO: if clause is unit, use its literal as selector
            # (ITotalizer must be extended to support PB constraints first)
            self.topv += 1
            selv = self.topv
            cl.append(self.topv)
            self.oracle.add_clause(cl)
            self.sels.append(selv)

        if self.verbose > 1:
            print('c formula: {0} vars, {1} hard, {2} soft'.format(formula.nv, len(formula.hard), len(formula.soft)))

    def __del__(self):
        """
            Destructor.
        """

        self.delete()

    def __enter__(self):
        """
            'with' constructor.
        """

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """
            'with' destructor.
        """

        self.delete()

    def delete(self):
        """
            Explicit destructor of the internal SAT oracle and the
            :class:`.ITotalizer` object.
        """

        if self.oracle:
            self.oracle.delete()
            self.oracle = None

        if self.tot:
            self.tot.delete()
            self.tot = None

    def solve(self):
        """
            Computes a solution to the MaxSAT problem. The method implements
            the LSU/LSUS algorithm, i.e. it represents a loop, each iteration
            of which calls a SAT oracle on the working MaxSAT formula and
            refines the upper bound on the MaxSAT cost until the formula
            becomes unsatisfiable.

            Returns ``True`` if the hard part of the MaxSAT formula is
            satisfiable, i.e. if there is a MaxSAT solution, and ``False``
            otherwise.

            :rtype: bool
        """

        is_sat = False

        while self.oracle.solve():
            is_sat = True
            self.model = self.oracle.get_model()
            self.cost = self._get_model_cost(self.formula, self.model)
            if self.verbose:
                print('o {0}'.format(self.cost))
                sys.stdout.flush()
            if self.cost == 0:      # if cost is 0, then model is an optimum solution
                break
            self._assert_lt(self.cost)
            self.oracle.set_phases(self.model)  # solution-based phase saving

        if is_sat:
            self.model = filter(lambda l: abs(l) <= self.formula.nv, self.model)
            if self.verbose:
                print('s OPTIMUM FOUND')
        elif self.verbose:
            print('s UNSATISFIABLE')

        return is_sat

    def get_model(self):
        """
            This method returns a model obtained during a prior satisfiability
            oracle call made in :func:`solve`.

            :rtype: list(int)
        """

        return self.model

    def _get_model_cost(self, formula, model):
        """
            Given a WCNF formula and a model, the method computes the MaxSAT
            cost of the model, i.e. the sum of weights of soft clauses that are
            unsatisfied by the model.

            :param formula: an input MaxSAT formula
            :param model: a satisfying assignment

            :type formula: :class:`.WCNF`
            :type model: list(int)

            :rtype: int
        """

        model_set = set(model)
        cost = 0

        for i, cl in enumerate(formula.soft):
            cost += formula.wght[i] if all(l not in model_set for l in filter(lambda l: abs(l) <= self.formula.nv, cl)) else 0

        return cost

    def _assert_lt(self, cost):
        """
            The method enforces an upper bound on the cost of the MaxSAT
            solution. This is done by encoding the sum of all soft clause
            selectors with the use the iterative totalizer encoding, i.e.
            :class:`.ITotalizer`. Note that the sum is created once, at the
            beginning. Each of the following calls to this method only enforces
            the upper bound on the created sum by adding the corresponding unit
            size clause. Each such clause is added on the fly with no restart
            of the underlying SAT oracle.

            :param cost: the cost of the next MaxSAT solution is enforced to be
                *lower* than this current cost

            :type cost: int
        """

        if self.tot == None:
            self.tot = ITotalizer(lits=self.sels, ubound=cost-1, top_id=self.topv)
            self.topv = self.tot.top_id

            for cl in self.tot.cnf.clauses:
                self.oracle.add_clause(cl)

        self.oracle.add_clause([-self.tot.rhs[cost-1]])

    def oracle_time(self):
        """
            Method for calculating and reporting the total SAT solving time.
        """

        return self.oracle.time_accum()


#
#==============================================================================
def parse_options():
    """
        Parses command-line options.
    """

    try:
        opts, args = getopt.getopt(sys.argv[1:], 'hms:v', ['help', 'model', 'solver=', 'verbose'])
    except getopt.GetoptError as err:
        sys.stderr.write(str(err).capitalize())
        print_usage()
        sys.exit(1)

    solver = 'g4'
    verbose = 1
    print_model = False

    for opt, arg in opts:
        if opt in ('-h', '--help'):
            print_usage()
            sys.exit(0)
        elif opt in ('-m', '--model'):
            print_model = True
        elif opt in ('-s', '--solver'):
            solver = str(arg)
        elif opt in ('-v', '--verbose'):
            verbose += 1
        else:
            assert False, 'Unhandled option: {0} {1}'.format(opt, arg)

    return print_model, solver, verbose, args


#
#==============================================================================
def print_usage():
    """
        Prints usage message.
    """

    print('Usage: ' + os.path.basename(sys.argv[0]) + ' [options] dimacs-file')
    print('Options:')
    print('        -h, --help       Show this message')
    print('        -m, --model      Print model')
    print('        -s, --solver     SAT solver to use')
    print('                         Available values: g3, g4, m22, mgh (default = g4)')
    print('        -v, --verbose    Be verbose')


#
#==============================================================================
def parse_formula(fml_file):
    """
        Parse and return MaxSAT formula.
    """

    if re.search('\.wcnf(\.(gz|bz2|lzma|xz))?$', fml_file):
        fml = WCNF(from_file=fml_file)
    else:  # expecting '*.cnf'
        fml = CNF(from_file=fml_file).weighted()

    return fml


#
#==============================================================================
if __name__ == '__main__':
    print_model, solver, verbose, files = parse_options()

    if files:
        formula = parse_formula(files[0])

        lsu = LSU(formula, solver=solver, verbose=verbose)
        if lsu.solve():
            if print_model:
                print('v ' + ' '.join([str(l) for l in lsu.get_model()]), '0')

        if verbose > 1:
            print('c oracle time: {0:.4f}'.format(lsu.oracle_time()))
    else:
        print_usage()
