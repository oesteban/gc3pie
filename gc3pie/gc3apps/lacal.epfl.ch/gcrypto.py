#! /usr/bin/env python
#
#   gcrypto.py -- Front-end script for submitting multiple Crypto jobs to SMSCG.
"""
Front-end script for submitting multiple ``gnfs-cmd`` jobs to SMSCG.
It uses the generic `gc3libs.cmdline.SessionBasedScript` framework.

See the output of ``gcrypto --help`` for program usage instructions.
"""
__version__ = '2.0.0-a1 version (SVN $Revision$)'
# summary of user-visible changes
__changelog__ = """
  2012-01-29:
    * Moved CryptoApplication from gc3libs.application

    * Restructured main script due to excessive size of initial
    jobs. SessionBaseScript generate a single SequentialTask.
    SequentialTask generated as many ParallelTasks as the whole range divided
    by the number of simultaneous active jobs.

    * Each ParallelTask lauches 'max_running' CryptoApplications
"""
__author__ = 'sergio.maffiolett@gc3.uzh.ch'
__docformat__ = 'reStructuredText'



# run script, but allow GC3Pie persistence module to access classes defined here;
# for details, see: http://code.google.com/p/gc3pie/issues/detail?id=95
if __name__ == "__main__":
    import gcrypto
    gcrypto.GCryptoScript().run()


# stdlib imports
import fnmatch
import logging
import os
import os.path
import sys
from pkg_resources import Requirement, resource_filename

# GC3Pie interface
import gc3libs
from gc3libs.cmdline import SessionBasedScript, existing_file, positive_int, nonnegative_int
from gc3libs import Application, Run, Task, RetryableTask
import gc3libs.exceptions
import gc3libs.application
from gc3libs.dag import SequentialTaskCollection, ParallelTaskCollection, ChunkedParameterSweep

DEFAULT_INPUTFILE_LOCATION="srm://dpm.lhep.unibe.ch/dpm/lhep.unibe.ch/home/crypto/lacal_input_files.tgz"
DEFAULT_GNFS_LOCATION="srm://dpm.lhep.unibe.ch/dpm/lhep.unibe.ch/home/crypto/gnfs-cmd_20120406"

class CryptoApplication(gc3libs.Application):
    """
    Represent a ``gnfs-cmd`` job that examines the range `start` to `start+extent`.

    LACAL's ``gnfs-cmd`` invocation::

      $ gnfs-cmd begin length nth

    performs computations for a range: *begin* to *begin+length*,
    and *nth* is the number of threads spwaned.

    The following ranges are of interest: 800M-1200M and 2100M-2400M.

    CryptoApplication(param, step, input_files_archive, output_folder, **kw)
    """

    def __init__(self, start, extent, gnfs_location, input_files_archive, output, **kw):

        gnfs_executable_name = os.path.basename(gnfs_location)

        # set some execution defaults...
        kw.setdefault('requested_cores', 4)
        kw.setdefault('requested_architecture', Run.Arch.X86_64)
        kw.setdefault('requested_walltime', 2)

        kw['jobname'] = "LACAL_%s" % str(start + extent)
        kw['output_dir'] = os.path.join(output, str(start + extent))

        # XXX: this will be changed once RTE will be validated
        # will use APPS/CRYPTO/LACAL-1.0
        # kw['tags'] = [ 'TEST/CRYPTO-1.0' ]
        # kw['tags'] = [ 'TEST/LACAL-1.0' ]
        kw['tags'] = [ 'APPS/CRYPTO/LACAL-1.0' ]

        gc3libs.Application.__init__(
            self,

            executable = "gnfs-cmd",
            executables = ["gnfs-cmd"],
            arguments = [ start, extent, kw['requested_cores'], "input.tgz" ],
            inputs = {
                input_files_archive:"input.tgz",
                gnfs_location:"gnfs-cmd",
                },
            outputs = [ '@output.list' ],
            # outputs = gc3libs.ANY_OUTPUT,
            stdout = 'gcrypto.log',
            join=True,
            **kw
            )

    def terminated(self):
        """
        Checks whether the ``M*.gz`` files have been created.

        The exit status of the whole job is set to one of these values:

        *  0 -- all files processed successfully
        *  1 -- some files were *not* processed successfully
        *  2 -- no files processed successfully
        * 127 -- the ``gnfs-cmd`` application did not run at all.

        """
        # XXX: need to gather more info on how to post-process.
        # for the moment do nothing and report job's exit status
        
        if self.execution.exitcode:
            gc3libs.log.debug(
                'Application terminated. postprocessing with execution.exicode %d',
                self.execution.exitcode)
        else:
            gc3libs.log.debug(
                'Application terminated. No exitcode available')

        if self.execution.signal == 123:
            # Assume Data staging problem
            # resubmit
            self.execution.returncode = (0, 99)
    
class CryptoTask(RetryableTask, gc3libs.utils.Struct):
    """
    Run ``gnfs-cmd`` on a given range
    """
    def __init__(self, start, extent, gnfs_location, input_files_archive, output, **kw):
        RetryableTask.__init__(
            self,
            # task name
            "LACAL_"+str(start), # jobname
            # actual computational job
            CryptoApplication(start, extent, gnfs_location, input_files_archive, output, **kw),
            # keyword arguments
            **kw)


    def retry(self):
        """
        Resubmit a cryto application instance iff it exited with code 99.

        *Note:* There is currently no upper limit on the number of
        resubmissions!
        """
        if self.task.execution.exitcode == 99:
            return True
        else:
            return False

class CryptoChunkedParameterSweep(ChunkedParameterSweep):
    """
    Provided the beginning of the range `range_start`, the end of the
    range `range_end`, the slice size of each job `slice`,
    `CryptoChunkedParameterSweep` creates `chunk_size`
    `CryptoApplication`s to be executed in parallel.

    Every update cycle it will check how many new CryptoApplication
    will have to be created (each of the launching in parallel
    DEFAULT_PARALLEL_RANGE_INCREMENT CryptoApplications) as the
    following rule: [ (end-range - begin_range) / step ] /
    DEFAULT_PARALLEL_RANGE_INCREMENT
    """

    def __init__(self, range_start, range_end, slice, chunk_size,
                 input_files_archive, gnfs_location, output_folder, grid=None, **kw):

        # remember for later
        self.range_end = range_end
        self.parameter_count_increment = slice * chunk_size
        self.input_files_archive = input_files_archive
        self.gnfs_location = gnfs_location
        self.output_folder = output_folder
        self.kw = kw

        ChunkedParameterSweep.__init__(
            self, kw['jobname'], range_start, range_end, slice, chunk_size, grid)

    def new_task(self, param, **kw):
        """
        Create a new `CryptoApplication` for computing the range
        `param` to `param+self.parameter_count_increment`.
        """
        # return CryptoApplication(
        return CryptoTask(
            param, self.step, self.gnfs_location, self.input_files_archive, self.output_folder, **self.kw.copy())


## the script itself

class GCryptoScript(SessionBasedScript):
    # this will be display as the scripts' `--help` text
    """
Like a `for`-loop, the ``gcrypto`` driver script takes as input
three mandatory arguments:

1. RANGE_START: initial value of the range (e.g., 800000000)
2. RANGE_END: final value of the range (e.g., 1200000000)
3. SLICE: extent of the range that will be examined by a single job (e.g., 1000)

For example::

  gcrypto 800000000 1200000000 1000

will produce 400000 jobs; the first job will perform calculations
on the range 800000000 to 800000000+1000, the 2nd one will do the
range 800001000 to 800002000, and so on.

Inputfile archive location (e.g. lfc://lfc.smscg.ch/crypto/lacal/input.tgz)
can be specified with the '-i' option. Otherwise a default filename
'input.tgz' will be searched in current directory.

Job progress is monitored and, when a job is done,
output is retrieved back to submitting host in folders named:
'range_start + (slice * actual step)'

The `gcrypto` command keeps a record of jobs (submitted, executed and
pending) in a session file (set name with the '-s' option); at each
invocation of the command, the status of all recorded jobs is updated,
output from finished jobs is collected, and a summary table of all
known jobs is printed.  New jobs are added to the session if new input
files are added to the command line.

Options can specify a maximum number of jobs that should be in
'SUBMITTED' or 'RUNNING' state; `gcrypto` will delay submission
of newly-created jobs so that this limit is never exceeded.
    """

    def __init__(self):
        SessionBasedScript.__init__(
            self,
            version = __version__, # module version == script version
            stats_only_for = CryptoApplication,
            )


    def setup_args(self):
        """
        Set up command-line argument parsing.

        The default command line parsing considers every argument as
        an (input) path name; processing of the given path names is
        done in `parse_args`:meth:
        """
        self.add_param('range_start', type=nonnegative_int,
                  help="Non-negative integer value of the range start.")
        self.add_param('range_end', type=positive_int,
                  help="Positive integer value of the range end.")
        self.add_param('slice', type=positive_int,
                  help="Positive integer value of the increment.")

    def parse_args(self):
        if self.params.range_end <= self.params.range_start:
            # Failed
            raise ValueError("End range cannot be smaller than Start range. Start range %d. End range %d" % (self.params.range_start, self.params.range_end))

    def setup_options(self):
        self.add_param("-i", "--input-files", metavar="PATH",
                       action="store", dest="input_files_archive",
                       default=DEFAULT_INPUTFILE_LOCATION,
                       help="Path to the input files archive."
                       " By default, the preloaded input archive available on"
                       " SMSCG Storage Element will be used: "
                       " %s" % DEFAULT_INPUTFILE_LOCATION)
        self.add_param("-g", "--gnfs-cmd", metavar="PATH",
                       action="store", dest="gnfs_location",
                       default=DEFAULT_GNFS_LOCATION,
                       help="Path to the executable script (gnfs-cmd)"
                       " By default, the preloaded gnfs-cmd available on"
                       " SMSCG Storage Element will be used: "
                       " %s" % DEFAULT_GNFS_LOCATION)


    def new_tasks(self, extra):
        yield (
            "LACAL_"+str(self.params.range_start), # jobname
            CryptoChunkedParameterSweep,
            [ # parameters passed to the constructor, see `CryptoSequence.__init__`
                self.params.range_start,
                self.params.range_end,
                self.params.slice,
                self.params.max_running, # increment of each ParallelTask
                self.params.input_files_archive, # path to input.tgz
                self.params.gnfs_location, # path to gnfs-cmd
                self.params.output, # output folder
                ],
            extra.copy()
            )


    def before_main_loop(self):
        """
        Ensure each instance of `ChunkedParameterSweep` has
        `chunk_size` set to the maximum allowed number of jobs.
        """
        for task in self.session:
            assert isinstance(task, CryptoChunkedParameterSweep)
            task.chunk_size = self.params.max_running
