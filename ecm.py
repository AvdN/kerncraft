#!/usr/bin/env python

from __future__ import print_function
from textwrap import dedent
from pprint import pprint
from functools import reduce
import operator
import math
import copy
import sys

from pycparser import CParser, c_ast
import sympy  # TODO remove dependency to sympy

import intervals

# Datatype sizes in bytes
datatype_size = {'double': 8, 'float': 4}


def prefix_indent(prefix, textblock, later_prefix=' '):
    textblock = textblock.split('\n')
    s = prefix + textblock[0] + '\n'
    if len(later_prefix) == 1:
        later_prefix = ' '*len(prefix)
    s = s+'\n'.join(map(lambda x: later_prefix+x, textblock[1:]))
    if s[-1] != '\n':
        return s + '\n'
    else:
        return s


def blocking(indices, block_size, initial_boundary=0):
    '''
    splits list of integers into blocks of block_size. returns block indices.
    
    first block element is located at initial_boundary (default 0).
    
    >>> blocking([0, -1, -2, -3, -4, -5, -6, -7, -8, -9], 8)
    [0,-1]
    >>> blocking([0], 8)
    [0]
    '''
    blocks = []
    
    for idx in indices:
        bl_idx = (idx-initial_boundary)/block_size
        if bl_idx not in blocks:
            blocks.append(bl_idx)
    blocks.sort()
    
    return blocks


def find(f, seq):
  """Return first item in sequence where f(item) == True."""
  for item in seq:
    if f(item): 
      return item
    

def flatten_dict(d):
    '''
    transforms 2d-dict d[i][k] into a new 1d-dict e[(i,k)] with 2-tuple keys
    '''
    e = {}
    for k in d.keys():
        for l in d[k].keys():
            e[(k,l)] = d[k][l]
    return e


class MachineModel:
    def __init__(self, name, arch, clock, cores, cl_size, mem_bw, cache_stack):
        '''
        *name* is the official name of the CPU
        *arch* is the archetecture name, this must be SNB, IVB or HSW
        *clock* is the number of cycles per second the CPU can perform
        *cores* is the number of cores
        *cl_size* is the number of bytes in one cache line
        *mem_bw* is the number of bytes per second that are read from memory to the lowest cache lvl
        *cache_stack* is a list of cache levels (tuple):
            (level, size, type, bw)
            *level* is the numerical id of the cache level
            *size* is the size of the cache
            *type* can be 'per core' or 'per socket'
            *cycles* is is the numbe of cycles to transfer one cache line from/to lower level
        '''
        self.name = name
        self.arch = arch
        self.clock = clock
        self.cores = cores
        self.cl_size = cl_size
        self.mem_bw = mem_bw
        self.cache_stack = cache_stack
    
    @classmethod
    def parse_dict(cls, input):
        # TODO
        input = {
        'name': 'Intel Xeon 2660v2',
        'clock': '2.2 GHz',
        'IACA architecture': 'IVB',
        'caheline': '64 B',
        'memory bandwidth': '60 GB/s',
        'cores': '10',
        'cache stack': 
            [{'size': '32 KB', 'type': 'per core', 'bw': '1 CL/cy'},
             {'size': '256 KB', 'type': 'per core', 'bw': '1 CL/cy'},
             {'size': '25 MB', 'type': 'per socket'}]
        }
        
        obj = cls(input['name'], arch=input['IACA architecture'], clock=input['clock'],
                  cores=input['cores'], cl_size=input['cacheline'],
                  mem_bw=input['memory bandwidth'], cache_stack=input['cache stack'])

class ArrayAccess:
    '''
    This class under stands array acceess based on multidimensional indices and one dimensional
    functions of style i*N+j
    '''
    def __init__(self, aref=None, array_info=None):
        '''If *aref* (and *array_info*) is present, this will be parsed.'''
        self.sym_expr = None
        self.index_parameters = {}
        if aref:
            self.parse(aref, array_info=array_info)
    
    def parse(self, aref, dim=0, array_info=None):
        if type(aref.name) is c_ast.ArrayRef:
            from_higher_dim = self.parse(aref.name, dim=dim+1, array_info=array_info)
        else:
            from_higher_dim = 0
        
        if array_info and dim != 0:
            dim_stride = reduce(lambda m,n: sympy.Mul(m, n, evaluate=False), array_info[1][:dim])
        else:
            dim_stride = 1
        
        sym_expr = sympy.Mul(conv_ast_to_sympy(aref.subscript), dim_stride, evaluate=False) + \
            from_higher_dim
        
        if dim == 0:
            # Store gathered information
            self.sym_expr = sym_expr
        else:
            # Return gathered information
            return sym_expr
    
    def extract_parameters(self):
        eq = self.sym_expr.simplify()
        terms = eq.as_ordered_terms()
        
        for t in terms:
            if type(t) is sympy.Symbol:
                self.index_parameters[t.name] = {}
            elif type(t) is sympy.Mul:
                for a in t.args:
                    if type(a) is sympy.Symbol:
                        self.index_parameters[a.name] = {}
                    elif type(a) is sympy.Integer:
                        #self.
                        pass
    
    def __repr__(self):
        return unicode(self.sym_expr)


def conv_ast_to_sympy(math_ast):
    '''
    converts mathematical expressions containing paranthesis, addition, subtraction and
    multiplication from AST to SymPy expresions.
    '''
    if type(math_ast) is c_ast.ID:
        return sympy.Symbol(math_ast.name)
    elif type(math_ast) is c_ast.Constant:
        return int(math_ast.value)
    else:  # elif type(dim) is c_ast.BinaryOp:
        sympy_op = {'*': lambda l,r: sympy.Mul(l, r, evaluate=False),
                    '+': lambda l,r: sympy.Add(l, r, evaluate=False),
                    '-': lambda l,r: sympy.Add(l, sympy.Mul(-1, r), evaluate=False)}
        
        op = sympy_op[math_ast.op]
        return op(conv_ast_to_sympy(math_ast.left), conv_ast_to_sympy(math_ast.right))


class Kernel:
    def __init__(self, kernel_code, constants=None, variables=None):
        '''This class captures the DSL kernel code, analyzes it and reports access pattern'''
        self.kernel_code = kernel_code
    
        parser = CParser()
        self.kernel_ast = parser.parse('void test() {'+kernel_code+'}').ext[0].body
        
        self._loop_stack = []
        self._sources = {}
        self._destinations = {}
        self._constants = {} if constants is None else constants
        self._variables = {} if variables is None else variables
    
    def set_constant(self, name, value):
        assert type(name) is str, "constant name needs to be of type str"
        assert type(value) is int, "constant value needs to be of type int"
        self._constants[name] = value
    
    def set_variable(self, name, type_, size):
        assert type_ in ['double', 'float'], 'only float and double variables are supported'
        assert type(size) in [tuple, type(None)], 'size has to be defined as tuple'
        self._variables[name] = (type_, size)
    
    def process(self):
        assert type(self.kernel_ast) is c_ast.Compound, "Kernel has to be a compound statement"
        assert all(map(lambda s: type(s) is c_ast.Decl, self.kernel_ast.block_items[:-1])), \
            'all statments befor the for loop need to be declarations'
        assert type(self.kernel_ast.block_items[-1]) is c_ast.For, \
            'last statment in kernel code must be a loop'
        
        for item in self.kernel_ast.block_items[:-1]:
            array = type(item.type) is c_ast.ArrayDecl
            
            if array:
                dims = []
                t = item.type
                while type(t) is c_ast.ArrayDecl:
                    dims.append(int(conv_ast_to_sympy(t.dim).subs(self._constants)))
                    t = t.type
                
                assert len(t.type.names) == 1, "only single types are supported"
                self.set_variable(item.name, t.type.names[0], tuple(dims))
                
            else:
                assert len(item.type.type.names) == 1, "only single types are supported"
                self.set_variable(item.name, item.type.type.names[0], None)
        
        floop = self.kernel_ast.block_items[-1]
        self._p_for(floop)
    
    def _get_offsets(self, aref, dim=0):
        '''
        returns a list of offsets of an ArrayRef object in all dimensions
        
        the index order is right to left (c-code order).
        e.g. c[i+1][j-2] -> [-2, +1]
        '''
        
        # Check for restrictions
        assert type(aref.name) in [c_ast.ArrayRef, c_ast.ID], \
            "array references must only be used with variables or other array references"
        assert type(aref.subscript) in [c_ast.ID, c_ast.Constant, c_ast.BinaryOp], \
            'array subscript must only contain variables or binary operations'
        
        idxs = []
        
        # TODO work-in-progress generisches auswerten von allem in [...]
        #idxs.append(('rel', conv_ast_to_sympy(aref.subscript)))
        if type(aref.subscript) is c_ast.BinaryOp:
            assert aref.subscript.op in '+-', \
                'binary operations in array subscript must by + or -'
            assert (type(aref.subscript.left) is c_ast.ID and \
                    type(aref.subscript.right) is c_ast.Constant), \
                'binary operation in array subscript may only have form "variable +- constant"'
            assert aref.subscript.left.name in map(lambda l: l[0], self._loop_stack), \
                'varialbes used in array indices has to be a loop counter'
            
            sign = 1 if aref.subscript.op == '+' else -1
            offset = sign*int(aref.subscript.right.value)
            
            idxs.append(('rel', aref.subscript.left.name, offset))
        elif type(aref.subscript) is c_ast.ID:
            assert aref.subscript.name in map(lambda l: l[0], self._loop_stack), \
                'varialbes used in array indices has to be a loop counter'
            idxs.append(('rel', aref.subscript.name, 0))
        else:  # type(aref.subscript) is c_ast.Constant
            idxs.append(('abs', int(aref.subscript.value)))
        
        if type(aref.name) is c_ast.ArrayRef:
            idxs += self._get_offsets(aref.name, dim=dim+1)
        
        if dim == 0:
            idxs.reverse()
        
        return idxs
    
    @classmethod
    def _get_basename(cls, aref):
        '''
        returns base name of ArrayRef object
        
        e.g. c[i+1][j-2] -> 'c'
        '''
        
        if type(aref.name) is c_ast.ArrayRef:
            return cls._get_basename(aref.name)
        else:
            return aref.name.name
    
    def _p_for(self, floop):
        # Check for restrictions
        assert type(floop) is c_ast.For, "May only be a for loop"
        assert hasattr(floop, 'init') and hasattr(floop, 'cond') and hasattr(floop, 'next'), \
            "Loop must have initial, condition and next statements."
        assert type(floop.init) is c_ast.Assignment, "Initialization of loops need to be " + \
            "assignments (declarations are not allowed or needed)"
        assert floop.cond.op in '<', "only lt (<) is allowed as loop condition"
        assert type(floop.cond.left) is c_ast.ID, 'left of cond. operand has to be a variable'
        assert type(floop.cond.right) in [c_ast.Constant, c_ast.ID, c_ast.BinaryOp], \
            'right of cond. operand has to be a constant, a variable or a binary operation'
        assert type(floop.next) in [c_ast.UnaryOp, c_ast.Assignment], 'next statement has to ' + \
            'be a unary or assignment operation'
        assert floop.next.op in ['++', 'p++', '+='], 'only ++ and += next operations are allowed'
        assert type(floop.stmt) in [c_ast.Compound, c_ast.Assignment, c_ast.For], 'the inner ' + \
            'loop may contain only assignments or compounds of assignments'

        if type(floop.cond.right) is c_ast.ID:
            const_name = floop.cond.right.name
            assert const_name in self._constants, 'loop right operand has to be defined as a ' +\
                 'constant in ECM object'
            iter_max = self._constants[const_name]
        elif type(floop.cond.right) is c_ast.Constant:
            iter_max = int(floop.cond.right.value)
        else:  # type(floop.cond.right) is c_ast.BinaryOp
            bop = floop.cond.right
            assert type(bop.left) is c_ast.ID, 'left of operator has to be a variable'
            assert type(bop.right) is c_ast.Constant, 'right of operator has to be a constant'
            assert bop.op in '+-', 'only plus (+) and minus (-) are accepted operators'
            
            sign = 1 if bop.op == '+' else -1
            iter_max = self._constants[bop.left.name]+sign*int(bop.right.value)
        
        if type(floop.next) is c_ast.Assignment:
            assert type(floop.next.lvalue) is c_ast.ID, \
                'next operation may only act on loop counter'
            assert type(floop.next.rvalue) is c_ast.Constant, 'only constant increments are allowed'
            assert floop.next.lvalue.name ==  floop.cond.left.name ==  floop.init.lvalue.name, \
                'initial, condition and next statement of for loop must act on same loop ' + \
                'counter variable'
            step_size = int(floop.next.rvalue.value)
        else:
            assert type(floop.next.expr) is c_ast.ID, 'next operation may only act on loop counter'
            assert floop.next.expr.name ==  floop.cond.left.name ==  floop.init.lvalue.name, \
                'initial, condition and next statement of for loop must act on same loop ' + \
                'counter variable'
            step_size = 1
        
        # Document for loop stack
        self._loop_stack.append(
            # (index name, min, max, step size)
            (floop.init.lvalue.name, floop.init.rvalue.value, iter_max, step_size)
        )
        # TODO add support for other stepsizes (even negative/reverse steps?)

        # Traverse tree
        if type(floop.stmt) is c_ast.For:
            self._p_for(floop.stmt)
        elif type(floop.stmt) is c_ast.Compound and \
                len(floop.stmt.block_items) == 1 and \
                type(floop.stmt.block_items[0]) is c_ast.For:
            self._p_for(floop.stmt.block_items[0])
        elif type(floop.stmt) is c_ast.Assignment:
            self._p_assignment(floop.stmt)
        else:  # type(floop.stmt) is c_ast.Compound
            for assgn in floop.stmt.block_items:
                self._p_assignment(assgn)

    def _p_assignment(self, stmt):
        # Check for restrictions
        assert type(stmt) is c_ast.Assignment, \
            "Only assignment statements are allowed in loops."
        assert type(stmt.lvalue) in [c_ast.ArrayRef, c_ast.ID], \
            "Only assignment to array element or varialbe is allowed."
        
        # Document data destination
        if type(stmt.lvalue) is c_ast.ArrayRef:
            # self._destinations[dest name] = [dest offset, ...])
            self._destinations.setdefault(self._get_basename(stmt.lvalue), [])
            self._destinations[self._get_basename(stmt.lvalue)].append(
                 self._get_offsets(stmt.lvalue))
            # TODO deactivated for now, since that notation might be useless
            # ArrayAccess(stmt.lvalue, array_info=self._variables[self._get_basename(stmt.lvalue)])
        else:  # type(stmt.lvalue) is c_ast.ID
            self._destinations.setdefault(stmt.lvalue.name, [])
            self._destinations[stmt.lvalue.name].append([('dir',)])
        
        # Traverse tree
        self._p_sources(stmt.rvalue)

    def _p_sources(self, stmt):
        sources = []
        
        assert type(stmt) in [c_ast.ArrayRef, c_ast.Constant, c_ast.ID, c_ast.BinaryOp], \
            'only references to arrays, constants and variables as well as binary operations ' + \
            'are supported'

        if type(stmt) is c_ast.ArrayRef:            
            # Document data source
            bname = self._get_basename(stmt)
            self._sources.setdefault(bname, [])
            self._sources[bname].append(self._get_offsets(stmt))
            # TODO deactivated for now, since that notation might be useless
            # ArrayAccess(stmt, array_info=self._variables[bname])
        elif type(stmt) is c_ast.ID:
            # Document data source
            self._sources.setdefault(stmt.name, [])
            self._sources[stmt.name].append([('dir',)])
        elif type(stmt) is c_ast.BinaryOp:
            # Traverse tree
            self._p_sources(stmt.left)
            self._p_sources(stmt.right)
        
        return sources
    
    def print_kernel_info(self):
        table = ('     idx |        min        max       step\n' +
                 '---------+---------------------------------\n')
        for l in self._loop_stack:
            table += '{:>8} | {:>10} {:>10} {:>+10}\n'.format(*l)
        print(prefix_indent('loop stack:        ', table))
        
        table = ('    name |  offsets   ...\n' +
                 '---------+------------...\n')
        for name, offsets in self._sources.items():
            prefix = '{:>8} | '.format(name)
            right_side = '\n'.join(map(lambda o: ', '.join(map(tuple.__repr__, o)), offsets))
            table += prefix_indent(prefix, right_side, later_prefix='         | ')
        print(prefix_indent('data sources:      ', table))
        
        table = ('    name |  offsets   ...\n' +
                 '---------+------------...\n')
        for name, offsets in self._destinations.items():
            prefix = '{:>8} | '.format(name)
            right_side = '\n'.join(map(lambda o: ', '.join(map(tuple.__repr__, o)), offsets))
            table += prefix_indent(prefix, right_side, later_prefix='         | ')
        print(prefix_indent('data destinations: ', table))

    def print_kernel_code(self):
        print(self.kernel_code)
    
    def print_variables_info(self):
        table = ('    name |   type size             \n' +
                 '---------+-------------------------\n')
        for name, var_info in self._variables.items():
            table += '{:>8} | {:>6} {:<10}\n'.format(name, var_info[0], var_info[1])
        print(prefix_indent('variables: ', table))
    
    def print_constants_info(self):
        table = ('    name | value     \n' +
                 '---------+-----------\n')
        for name, value in self._constants.items():
            table += '{:>8} | {:<10}\n'.format(name, value)
        print(prefix_indent('constants: ', table))

class ECM:
    """
    class representation of the Execution-Cache-Memory Model

    more info to follow...
    """

    def __init__(self, kernel, core, machine):
        """
        *kernel* is a Kernel object
        *core* is the  in-core throughput as tuple of (overlapping cycles, non-overlapping cycles)
        *machine* describes the machine (cpu, cache and memory) characteristics
        """
        self.kernel = kernel
        self.core = core
        self.machine = machine
    
    def _calculate_relative_offset(self, name, access_dimensions):
        '''
        returns the offset from the iteration center in number of elements and the order of indices
        used in access.
        '''
        offset = 0
        base_dims = self.kernel._variables[name][1]
        
        for dim, offset_info in enumerate(access_dimensions):
            offset_type, idx_name, dim_offset = offset_info
            assert offset_type == 'rel', 'Only relative access to arrays is supported at the moment'
            
            if offset_type == 'rel':
                offset += dim_offset*reduce(operator.mul, base_dims[dim+1:], 1)
            else:
                # should not happen
                pass
        
        return offset
    
    def _calculate_iteration_offset(self, name, index_order, loop_index):
        '''
        returns the offset from one to the next iteration using *loop_index*.
        *index_order* is the order used by the access dimensions e.g. 'ijk' corresponse to [i][j][k]
        *loop_index* specifies the loop to be used for iterations (this is typically the inner 
        moste one)
        '''
        offset = 0
        base_dims = self.kernel._variables[name][1]
            
        for dim, index_name in enumerate(index_order):
            if loop_index == index_name:
                offset += reduce(operator.mul, base_dims[dim+1:], 1)
        
        return offset
    
    def _get_index_order(self, access_dimensions):
        '''Returns the order of indices used in *access_dimensions*.'''
        return ''.join(map(lambda d: d[1], access_dimensions))
    
    def _expand_to_cacheline_blocks(self, first, last):
        '''
        Returns first and last values wich align with cacheline blocks, by increasing range.
        '''
        # TODO how to handle multiple datatypes (with different size)?
        element_size = datatype_size['double']
        elements_per_cacheline = self.machine.cl_size / element_size
        
        first = first - first%elements_per_cacheline
        last = last - last%elements_per_cacheline + elements_per_cacheline - 1
        
        return [first, last]
    
    def calculate_cache_access(self):
        results = {}
        
        read_offsets = {var_name: dict() for var_name in self.kernel._variables.keys()}
        write_offsets = {var_name: dict() for var_name in self.kernel._variables.keys()}
        iteration_offsets = {var_name: dict() for var_name in self.kernel._variables.keys()}
        
        # TODO how to handle multiple datatypes (with different size)?
        element_size = datatype_size['double']
        elements_per_cacheline = self.machine.cl_size / element_size
        
        loop_order = ''.join(map(lambda l: l[0], self.kernel._loop_stack))
        
        for var_name in self.kernel._variables.keys():
            var_type, var_dims = self.kernel._variables[var_name]
            
            # Skip the following access: (they are hopefully kept in registers)
            #   - scalar values
            if var_dims is None: continue
            #   - access does not change with inner-most loop index
            writes = filter(lambda acs: loop_order[-1] in map(lambda a: a[1], acs),
                self.kernel._destinations.get(var_name, []))
            reads = filter(lambda acs: loop_order[-1] in map(lambda a: a[1], acs),
                self.kernel._sources.get(var_name, []))
            
            # Compile access pattern
            for r in reads:
                offset = self._calculate_relative_offset(var_name, r)
                idx_order = self._get_index_order(r)
                read_offsets[var_name].setdefault(idx_order, [])
                read_offsets[var_name][idx_order].append(offset)
            for w in writes:
                offset = self._calculate_relative_offset(var_name, w)
                idx_order = self._get_index_order(w)
                write_offsets[var_name].setdefault(idx_order, [])
                write_offsets[var_name][idx_order].append(offset)
            
            # Do unrolling so that one iteration equals one cacheline worth of workload:
            # unrolling is done on inner-most loop only!
            for i in range(1, elements_per_cacheline):
                for r in reads:
                    idx_order = self._get_index_order(r)
                    offset = self._calculate_relative_offset(var_name, r)
                    offset += i * self._calculate_iteration_offset(
                        var_name, idx_order, loop_order[-1])
                    read_offsets[var_name][idx_order].append(offset)
            
                    # Remove multiple access to same offsets
                    read_offsets[var_name][idx_order] = \
                        sorted(list(set(read_offsets[var_name][idx_order])), reverse=True)
                
                for w in writes:
                    idx_order = self._get_index_order(w)
                    offset = self._calculate_relative_offset(var_name, w)
                    offset += i * self._calculate_iteration_offset(
                        var_name, idx_order, loop_order[-1])
                    write_offsets[var_name][idx_order].append(offset)
                    
                    # Remove multiple access to same offsets
                    write_offsets[var_name][idx_order] = \
                        sorted(list(set(write_offsets[var_name][idx_order])), reverse=True)
        
        # initialize misses and hits
        misses = {}
        hits = {}
        evicts = {}
        total_misses = {}
        total_hits = {}
        total_evicts = {}
        total_lines_misses = {}
        total_lines_hits = {}
        total_lines_evicts = {}
        
        # Check for layer condition towards all cache levels
        for cache_level, cache_size, cache_type, cache_cycles in self.machine.cache_stack:
            trace_length = 0
            updated_length = True
            while updated_length:
                updated_length = False
                
                # Initialize cache, misses, hits and evicts for current level
                cache = {}
                misses[cache_level] = {}
                hits[cache_level] = {}
                evicts[cache_level] = {}
                
                # We consider everythin a miss in the beginning
                # TODO here read and writes are treated the same, this implies write-allocate
                #      to support nontemporal stores, this needs to be changed
                for name in read_offsets.keys()+write_offsets.keys():
                    cache[name] = {}
                    misses[cache_level][name] = {}
                    hits[cache_level][name] = {}

                    for idx_order in read_offsets[name].keys()+write_offsets[name].keys():
                        cache[name][idx_order] = intervals.Intervals()
                        if cache_level-1 not in misses:
                            misses[cache_level][name][idx_order] = sorted(
                                read_offsets.get(name, {}).get(idx_order, []) + 
                                write_offsets.get(name, {}).get(idx_order, []),
                                reverse=True)
                        else:
                            misses[cache_level][name][idx_order] = \
                                 list(misses[cache_level-1][name][idx_order])
                        hits[cache_level][name][idx_order] = []
                
                # Caches are still empty (thus only misses)
                trace_count = 0
                cache_used_size = 0
                
                # Now we trace the cache access backwards (in time/iterations) and check for hits
                for var_name in misses[cache_level].keys():
                    for idx_order in misses[cache_level][var_name].keys():
                        iter_offset = self._calculate_iteration_offset(
                            var_name, idx_order, loop_order[-1])
                        
                        # Add cache trace
                        for offset in list(misses[cache_level][var_name][idx_order]):
                            # If already present in cache add to hits
                            if offset in cache[var_name][idx_order]:
                                misses[cache_level][var_name][idx_order].remove(offset)
                                
                                # We might have multiple hits on the same offset (e.g in DAXPY)
                                if offset not in hits[cache_level][var_name][idx_order]:
                                    hits[cache_level][var_name][idx_order].append(offset)
                                
                            # Add cache, we can do this since misses are sorted in reverse order of
                            # access and we assume LRU cache replacement policy
                            if iter_offset <= elements_per_cacheline:
                                # iterations overlap, thus we can savely add the whole range
                                cached_first, cached_last = self._expand_to_cacheline_blocks(
                                    offset-iter_offset*trace_length, offset+1)
                                cache[var_name][idx_order] &= intervals.Intervals(
                                    [cached_first, cached_last+1], sane=True)
                            else:
                                # There is no overlap, we can append the ranges onto one another
                                # TODO optimize this code section (and maybe merge with above)
                                new_cache = [self._expand_to_cacheline_blocks(o, o) for o in range(
                                    offset-iter_offset*trace_length, offset+1, iter_offset)]
                                new_cache = intervals.Intervals(*new_cache, sane=True)
                                cache[var_name][idx_order] &= new_cache
                                
                        trace_count += len(cache[var_name][idx_order]._data)
                        cache_used_size += len(cache[var_name][idx_order])*element_size
                
                # Calculate new possible trace_length according to free space in cache
                # TODO take CL blocked access into account
                # TODO make /2 customizable
                new_trace_length = trace_length + \
                    ((cache_size/2 - cache_used_size)/trace_count)/element_size
                
                if new_trace_length > trace_length:
                    trace_length = new_trace_length
                    updated_length = True
                
                # All writes to require the data to be evicted eventually
                evicts[cache_level] = \
                    {var_name: dict() for var_name in self.kernel._variables.keys()}
                for name in write_offsets.keys():
                    for idx_order in write_offsets[name].keys():
                        evicts[cache_level][name][idx_order] = list(write_offsets[name][idx_order])
            
            # Compiling stats
            total_misses[cache_level] = sum(map(lambda l: sum(map(len, l.values())),
                misses[cache_level].values()))
            total_hits[cache_level] = sum(map(lambda l: sum(map(len, l.values())),
                hits[cache_level].values()))
            total_evicts[cache_level] = sum(map(lambda l: sum(map(len, l.values())),
                evicts[cache_level].values()))
            
            total_lines_misses[cache_level] = sum(map(
                lambda o: sum(map(lambda n: len(blocking(n, elements_per_cacheline)), o.values())),
                misses[cache_level].values()))
            total_lines_hits[cache_level] = sum(map(lambda o: sum(map(lambda n:
                len(blocking(n, elements_per_cacheline)), o.values())),
                hits[cache_level].values()))
            total_lines_evicts[cache_level] = sum(map(lambda o: sum(map(lambda n: 
                len(blocking(n,elements_per_cacheline)), o.values())),
                evicts[cache_level].values()))
            
            print('Trace legth per access in L{}:'.format(cache_level), trace_length)
            print('Hits in L{}:'.format(cache_level), total_hits[cache_level], hits[cache_level])
            print('Misses in L{}: {} ({}CL):'.format(
                cache_level, total_misses[cache_level], total_lines_misses[cache_level]), 
                misses[cache_level])
            print('Evicts from L{} {} ({}CL):'.format(
                cache_level, total_evicts[cache_level], total_lines_evicts[cache_level]),
                evicts[cache_level])
            
            if cache_cycles:
                results['L{}-L{}'.format(cache_level, cache_level+1)] = round(
                    (total_lines_misses[cache_level]+total_lines_evicts[cache_level]) *
                    cache_cycles, 1)
                    
                print('Cycles L{}-L{}:'.format(cache_level, cache_level+1), round(
                    (total_lines_misses[cache_level]+total_lines_evicts[cache_level]) *
                    cache_cycles, 1))
                
            else:
                results['L{}-MEM'.format(cache_level)] = round(
                    (total_lines_misses[cache_level]+total_lines_evicts[cache_level]) *
                    elements_per_cacheline*element_size/self.machine.mem_bw*self.machine.clock, 1)
                
                print('Cycles L{}-MEM:'.format(cache_level), round(
                    (total_lines_misses[cache_level]+total_lines_evicts[cache_level]) *
                    elements_per_cacheline*element_size/self.machine.mem_bw*self.machine.clock, 1))
            
        return results


# Example kernels:
kernels = {
    'DAXPY':
        {
            'kernel_code':
                """\
                double a[N], b[N];
                double s;
                
                for(i=0; i<N; ++i)
                    a[i] = a[i] + s * b[i];
                """,
            'testcase': [{
                'constants': [('N', 50)],
                'results-to-compare': {
                    'CPU': 2,
                    'REG-L1': 4,
                    'L1-L2': 6,
                    'L2-L3': 6,
                    'L3-MEM': 13},
                }],
        },
    'scale':
        {
            'kernel_code':
                """\
                double a[N], b[N];
                double s;
                
                for(i=0; i<N; ++i)
                    a[i] = s * b[i];
                """,
            'testcase': [{'constants': [('N', 50)],}],
            
        },
    'copy':
        {
            'kernel_code':
                """\
                double a[N], b[N];
                
                for(i=0; i<N; ++i)
                    a[i] = b[i];
                """,
            'testcase': [{'constants': [('N', 50)],}],
        },
    'add':
        {
            'kernel_code':
                """\
                double a[N], b[N], c[N];
                
                for(i=0; i<N; ++i)
                    a[i] = b[i] + c[i];
                """,
            'testcase': [{'constants': [('N', 50)],}],
        },
    'triad':
        {
            'kernel_code':
                """\
                double a[N], b[N], c[N];
                double s;
                
                for(i=0; i<N; ++i)
                    a[i] = b[i] + s * c[i];
                """,
            'testcase': [{'constants': [('N', 50)],}],
        },
    '1d-3pt':
        {
            'kernel_code':
                """\
                double a[N], b[N];
                
                for(i=1; i<N-1; ++i)
                    b[i] = c * (a[i-1] - 2.0*a[i] + a[i+1]);
                """,
            'testcase': [{'constants': [('N', 50)],}],
        },
    '2d-5pt':
        {
            'kernel_code':
                """\
                double a[N][N];
                double b[N][N];
                double c;
                
                for(j=1; j<N-1; ++j)
                    for(i=1; i<N-1; ++i)
                        b[j][i] = ( a[j][i-1] + a[j][i+1]
                                  + a[j-1][i] + a[j+1][i]) * s;
                """,
            'testcase': [ # See log/2014-12-12.md
                { 
                    # L1
                    'constants': [('N', 511)], 
                    'results-to-compare': {
                        'T_nOL': 6,
                        'T_OL': 8,
                        'L1-L2': 6,
                        'L2-L3': 6,
                        'L3-MEM': 13},
                },
                {
                    # L2
                    'constants': [('N', 4094)],
                    'results-to-compare': {
                        'T_nOL': 6,
                        'T_OL': 8,
                        'L1-L2': 10,
                        'L2-L3': 6,
                        'L3-MEM': 13},
                },
                {
                    # L3
                    'constants': [('N', 327677)],
                    'results-to-compare': {
                        'T_nOL': 6,
                        'T_OL': 8,
                        'L1-L2': 10,
                        'L2-L3': 10,
                        'L3-MEM': 13},
                },
                {
                    # MEM
                    'constants': [('N', 327681)],
                    'results-to-compare': {
                        'T_nOL': 6,
                        'T_OL': 8,
                        'L1-L2': 10,
                        'L2-L3': 10,
                        'L3-MEM': 22},
                },
            ],
        },
    'uxx-stencil':
        {
            'kernel_code':
                """\
                double u1[N][N][N];
                double d1[N][N][N];
                double xx[N][N][N];
                double xy[N][N][N];
                double xz[N][N][N];
                double c1, c2, d;
                
                for(k=2; k<N-2; k++) {
                    for(j=2; j<N-2; j++) {
                        for(i=2; i<N-2; i++) {
                            d = 0.25*(d1[ k ][j][i] + d1[ k ][j-1][i]
                                    + d1[k-1][j][i] + d1[k-1][j-1][i]);
                            u1[k][j][i] = u1[k][j][i] + (dth/d)
                             * ( c1*(xx[ k ][ j ][ i ] - xx[ k ][ j ][i-1])
                               + c2*(xx[ k ][ j ][i+1] - xx[ k ][ j ][i-2])
                               + c1*(xy[ k ][j+1][ i ] - xy[ k ][j-1][ i ])
                               + c2*(xy[ k ][j+1][ i ] - xy[ k ][j-2][ i ])
                               + c1*(xz[ k ][ j ][ i ] - xz[k-1][ j ][ i ])
                               + c2*(xz[k+1][ j ][ i ] - xz[k-2][ j ][ i ]));
                }}}
                """,
            'testcase': [{ # DP
                'constants': [('N', 100)],
                'results-to-compare': {
                    'T_nOL': 84,
                    'T_OL': 38,
                    'L1-L2': 20,
                    'L2-L3': 20,
                    'L3-MEM': 26
                },
            }], # TODO add results for SP
        },
    # TODO Work-in-progress beispiel fuer zugriff ueber 1d-arrays
    #'uxx-stencil-expr':
    #    {
    #        'kernel_code':
    #            """\
    #            double u1[N*N*N];
    #            double d1[N*N*N];
    #            double xx[N*N*N];
    #            double xy[N*N*N];
    #            double xz[N*N*N];
    #            double c1, c2, d;
    #            
    #            for(k=2; k<N-2; k++) {
    #                for(j=2; j<N-2; j++) {
    #                    for(i=2; i<N-2; i++) {
    #                        d = 0.25*(d1[ k ][j][i] + d1[ k ][j-1][i]
    #                                + d1[k-1][j][i] + d1[k-1][j-1][i]);
    #                        u1[k][j][i] = u1[k][j][i] + (dth/d)
    #                         * ( c1*(xx[k*N*N     + j*N     + i]   - xx[k*N*N     + j       + i-1])
    #                           + c2*(xx[k*N*N     + j*N     + i+1] - xx[k*N*N     + j       + i-2])
    #                           + c1*(xy[k*N*N     + j*N     + i]   - xy[k*N*N     + (j-1)*N + i])
    #                           + c2*(xy[k*N*N     + (j+1)*N + i]   - xy[k*N*N     + (j-2)*N + i])
    #                           + c1*(xz[k*N*N     + j*N     + i]   - xz[(k-1)*N*N + j*N     + i])
    #                           + c2*(xz[(k+1)*N*N + j*N     + i]   - xz[(k-2)*N*N + j*N     + i]));
    #            }}}
    #            """,
    #        'testcase': [{'constants': [('N', 100)],}],
    #    },
    'matsq':
        {
            'kernel_code':
                """\
                double S[N][N];
                double D[N][N];
                
                for(i=0; i<N; i++) {
                    for(j=0; j<N; j++) {
                        for(k=0; k<N; k++) {
                            D[i][j] = D[i][j] + S[i][k]*S[k][j];
                        }
                    }
                }
                """,
            'testcase': [{'constants': [('N', 1000)],}],
        },
    '3d-long-range-stencil':
        {
            'kernel_code':
                """\
                double U[N][N][N];
                double V[N][N][N];
                double ROC[N][N][N];
                double c0, c1, c2, c3, c4, lap;
                
                for(k=4; k < N-4; k++) {
                    for(j=4; j < N-4; j++) {
                        for(i=4; i < N-4; i++) {
                            lap = c0 * V[k][j][i]
                                + c1 * ( V[ k ][ j ][i+1] + V[ k ][ j ][i-1])
                                + c1 * ( V[ k ][j+1][ i ] + V[ k ][j-1][ i ])
                                + c1 * ( V[k+1][ j ][ i ] + V[k-1][ j ][ i ])
                                + c2 * ( V[ k ][ j ][i+2] + V[ k ][ j ][i-2])
                                + c2 * ( V[ k ][j+2][ i ] + V[ k ][j-2][ i ])
                                + c2 * ( V[k+2][ j ][ i ] + V[k-2][ j ][ i ])
                                + c3 * ( V[ k ][ j ][i+3] + V[ k ][ j ][i-3])
                                + c3 * ( V[ k ][j+3][ i ] + V[ k ][j-3][ i ])
                                + c3 * ( V[k+3][ j ][ i ] + V[k-3][ j ][ i ])
                                + c4 * ( V[ k ][ j ][i+4] + V[ k ][ j ][i-4])
                                + c4 * ( V[ k ][j+4][ i ] + V[ k ][j-4][ i ])
                                + c4 * ( V[k+4][ j ][ i ] + V[k-4][ j ][ i ]);
                            U[k][j][i] = 2.f * V[k][j][i] - U[k][j][i] 
                                       + ROC[k][j][i] * lap;
                }}}
                """,
            'testcase': [{
                'constants': [('N', 100)],
                'results-to-compare': {
                    'T_nOL': 68,
                    'T_OL': 64,
                    'L1-L2': 24,
                    'L2-L3': 24,
                    'L3-MEM': 17
                },
            }],
        },
    }

if __name__ == '__main__':
    for name, info in kernels.items():
        for test in info['testcase']:
            print('='*80 + '\n{:^80}\n'.format(name) + '='*80)
            # Read machine description
            machine = {
                'name': 'Intel Xeon 2660v2',
                'clock': '2.2 GHz',
                'IACA architecture': 'IVB',
                'caheline': '64 B',
                'memory bandwidth': '60 GB/s',
                'cache stack': 
                    [{'level': 1, 'size': '32 KB', 'type': 'per core', 'bw': '1 CL/cy'},
                     {'level': 2, 'size': '256 KB', 'type': 'per core', 'bw': '1 CL/cy'},
                     {'level': 3, 'size': '25 MB', 'type': 'per socket'}]
            }
            # TODO support format as seen above
            # TODO missing in description bw_type, size_type, read and write bw between levels
            #      and cache size sharing and cache bw sharing
            #machine = MachineModel('Intel Xeon 2660v2', 'IVB', 2.2e9, 10, 64, 60e9, 
            #                       [(1, 32*1024, 'per core', 2),
            #                        (2, 256*1024, 'per core', 2),
            #                        (3, 25*1024*1024, 'per socket', None)])
            # SNB machine as described in ipdps15-ECM.pdf
            machine = MachineModel('Xeon E5-2680', 'SNB', 2.7e9, 8, 64, 40e9,
                                   [(1, 32*1024, 'per core', 2),
                                    (2, 256*1024, 'per core', 2),
                                    (3, 20*1024*1024, 'per socket', None)])
            
            # Read (run?) and interpret IACA output
            # TODO
            
            # Create ECM object and give additional information about runtime
            kernel = Kernel(dedent(info['kernel_code']))
            for const_name, const_value in test['constants']:
                kernel.set_constant(const_name, const_value)
            
            # Verify code and document data access and loop traversal
            kernel.process()
            kernel.print_kernel_code()
            print()
            kernel.print_variables_info()
            kernel.print_constants_info()
            kernel.print_kernel_info()
            
            # Analyze access patterns (in regard to cache sizes with layer conditions)
            ecm = ECM(kernel, None, machine)
            results = ecm.calculate_cache_access()  # <-- this is my thesis
            if 'results-to-compare' in test:
                for key, value in results.items():
                    if key in test['results-to-compare']:
                        correct_value = test['results-to-compare'][key]
                        diff = abs(value - correct_value)
                        if diff > correct_value*0.1:
                            print("Test values did not match: {} ".format(key) +
                                "should have been {}, but was {}.".format(correct_value, value))
                            sys.exit(1)
                        elif diff:
                            print("Small difference from theoretical value: {} ".format(key) +
                                "should have been {}, but was {}.".format(correct_value, value))
            
            # Report
            # TODO
            
            print
    
