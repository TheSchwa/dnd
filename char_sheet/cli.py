#!/usr/bin/env python3

# ===== WORKING EXAMPLES =====
#
# >>> new
# >>> set stat dexterity 17
# >>> get stat ac
# r-  13 ac (b:0/0 ?:0/0)
#
# >>> g s ac
# r-  13 ac (b:0/0 ?:0/0)
#
# >>> add bonus mage_armor 4 ac armor
# >>> get stat ac,ac_ff,ac_touch
# r-  17 ac (b:1/1 ?:0/0)
# r-  14 ac_ff (b:1/1 ?:0/0)
# r-  13 ac_touch (b:0/0 ?:0/0)
#
# >>> off mage_armor
# >>> get stat ac,ff,touch
# r-  13 ac (b:1/1 ?:0/0)
# r-  10 ac_ff (b:1/1 ?:0/0)
# r-  13 ac_touch (b:0/0 ?:0/0)
#
# >>> all bonus mage_armor
#   value | 4
#  active | False
#    type | armor
#  revert | same
#   stats | _ac_armor
# conditn |
#    text |
#
# ===== PLANNED FOR THE FUTURE =====
#
# >>> get stat ac,ff,touch
# r-  13 ac (b:0/1 ?:0/0)
# r-  10 ac_ff (b:0/1 ?:0/0)
# r-  13 ac_touch (b:0/0 ?:0/0)
#
# >>> get ac,ac_ff,ac_touch with mage_armor
# r-  17 ac (b:1/1 ?:0/0)
# r-  14 ac_ff (b:1/1 ?:0/0)
# r-  13 ac_touch (b:0/0 ?:0/0)

# ===== QUICK COMMAND LISTING =====
#
# implemented types: stat bonus effect text
#
# help [command [sub-command]]
# load [filename]
# new [system]
# save [filename]
# roll [dice]
# exit
#
# search [name]
# get [type] [name]
# set [type] name [options]
# add type name [options]
# del type name
# all type name
# on bonus
# off bonus
# revert bonus
#
# eval command
# exec command
# trace
#
# ===== NOT IMPLEMENTED =====
# time num [rd|min|hr|day]
# ++
# use name
# reset [name]
# revert

# ===== TODO =====
#
# [TODO] help/list aliases and/or pre-made/custom views (show all abilities)
# [TODO] autocompletions
# [TODO] consider managing multiple characters
# [TODO] better exception messages, especially for num args
# [TODO] custom aliases?
# [TODO] actual modificaion tracking for save prompting
# [TODO] review load/save logic and kill "old" methods

import sys,os,pickle,cmd,inspect,traceback
import logging
from argparse import ArgumentParser

import environ
import dnd.char_sheet.char as char
from dnd.char_sheet.dec import arbargs
from dnd.dice import Dice

###############################################################################
# main loop
#   - keep running forever unless we get EOF from the CLI (i.e. a clean return)
#   - if we get a KeyboardInterrupt or EOFError resume the loop
###############################################################################

def main(args=None):

  args = get_args(args or [])

  cli = CLI(**args)
  run = True
  print('')
  while run:
    try:
      cli.cmdloop()
      run = False
    except (KeyboardInterrupt, EOFError):
      print('\n')
      cli.output('*** Type "exit" / Use Ctrl+D at the main prompt to exit')
      print('')
      pass

# @param args (list) list of arguments to parse
def get_args(args):

  if isinstance(args, dict):
    return args

  ap = ArgumentParser()
  add = ap.add_argument

  add('file_name', nargs='?',
    help='Character file to edit')

  add('-l', '--log-file',
    help='File to use for logging')
  add('-d', '--debug', action='store_true',
    help='Enable debug logging (requires -l)')

  return vars(ap.parse_args(args))

###############################################################################
# exceptions and connectors
###############################################################################

# thrown when the user passes non-matching arguments to a command
# @param sig (str) the signature of the target command
# @param msg (str) error message
class ArgsError(Exception):
  def __init__(self,sig,msg):
    super(ArgsError,self).__init__(msg)
    self.sig = sig

# the Cmd class expects a static string but let's bootstrap it to be dynamic
class Prompt(object):

  # @param func (func) the function to generate the prompt
  def __init__(self,func):
    self.func = func

  # the Cmd class just calls a print, so we can trick it with this override
  # @return (str)
  def __str__(self):
    return str(self.func())

###############################################################################
# CLI class
#   - does alot of customization to the base cmd.Cmd
#   - dynamically loads commands from a Character
#   - advanced argument parsing
#     - quote blocking
#     - creates lists from any arg with commas that wasn't quoted
#     - keyword args e.g. "text=foo"
###############################################################################

class CLI(cmd.Cmd):

###############################################################################
# CLI overrides
###############################################################################

  # @param file_name (str) [None] file name to load
  # @param log_file (str) [None] file to use for logging
  # @param debug (bool) [False] enable debug logging
  def __init__(self, file_name=None, log_file=None, debug=False):

    cmd.Cmd.__init__(self)
    self.prompt = Prompt(self.get_prompt)
    self.indent = '  | '
    self.doc_header = 'General commands:'
    self.misc_header = 'Explanations:'

    self.fname = file_name
    self.log_file = log_file
    self.char = None
    self.exported = {}
    self.modified = False
    self.last_trace = 'No logged traceback'

    self.logger = None
    if self.log_file:
      logging.basicConfig(filename=self.log_file,
        format='%(asctime).19s | %(levelname).3s | %(message)s',
        level=(logging.DEBUG if debug else logging.INFO)
      )
      self.logger = logging.getLogger()

    if self.fname:
      self.do_load([self.fname])

    self.debug_flags = {
        'args' : False
    }

  # we expect this to get overriden by our Character
  # @return (str)
  def get_prompt(self):

    if not self.char:
      return '[ --- none --- ] '

    prompt = self.char._get_prompt()

    # the readline library doesn't seem to like prompts containing newlines
    # so print them manually and only return the final line to our parent
    if '\n' in prompt:
      prompt = prompt.split('\n')
      while len(prompt)>1:
        print(prompt.pop(0))
      prompt = prompt[0]
    return prompt

  # just print the prompt again if the user enters nothing
  def emptyline(self):
    pass

  # @param line (str)
  # @return (4-tuple)
  #   #0 (str) command name
  #   #1 (list) args to pass to the command
  #   #2 (dict) kwargs to pass to the command
  #   #3 (str) the original line
  def parseline(self,line):

    (args,kwargs) = self.get_args(line)
    if self.debug_flags['args']:
      self.output(args)
      self.output(kwargs)
      print('')

    return (args,kwargs)

  # parse input line into args and kwargs,
  # then try to execute exported character commands
  # if none match or it returns NotImplemented, execute a self.do_*
  # if still no match print message and exit via self.default()
  # @param line (str)
  def onecmd(self,line):

    if not line:
      return self.emptyline()
    self.debug('USERINPUT "%s"' % line)

    print('')
    try:
      (args,kwargs) = self.parseline(line)
      if args:
        self.debug('    ARGS %s' % args)
      if kwargs:
        self.debug('    KWARGS %s' % kwargs)
    except ArgsError as e:
      s = '*** ArgsError: %s' % e.args[0]
      self.output(s)
      self.log_exc(s, traceback.format_exc(), 'debug')
      return

    try:
      func = self.get_cli_cmd(args)
      (char_func,char_args) = self.get_char_cmd(args,kwargs,func)
      if self.debug_flags['args']:
        self.output(func)
        self.output(char_func)
        print('')
      if char_func:
        self.info('    CHARCMD %s' % char_func.__name__)
        self.check_args(char_func,char_args,kwargs,
            getattr(char_func,'_arbargs',False))
        result = char_func(*char_args,**kwargs)
        if result!=NotImplemented:
          if result:
            self.output(result)
          return

      if func:
        self.info('    CLICMD %s' % func.__name__)
        return func(args[1:])

    except ArgsError as e:
      s = '*** ArgsError: %s' % e.args[0]
      self.output(s)
      self.output('    %s' % e.sig)
      self.log_exc(s, traceback.format_exc(), 'debug')
      return

    except KeyboardInterrupt:
      print('')

    except:
      s = traceback.format_exc()
      self.last_trace = s.strip()
      s = s.split('\n')[-2]
      if '.' in s.split(':')[0]:
        s = s[s.rindex('.',0,s.index(':'))+1:]
      self.output('*** '+s)
      self.log_exc(s, self.last_trace)
      return

    return self.default(args)

  # @param args (list)
  # @return (2-tuple)
  #   #0 (func)
  #   #1 (list)
  def get_cli_cmd(self,args):

    try:
      return getattr(self,'do_'+args[0])
    except AttributeError:
      return None

  # @param args (list)
  # @param kwargs (dict)
  # @param silent (bool) whether to print errors
  # @return (2-tuple)
  #   #0 (func)
  #   #1 (list)
  def get_char_cmd(self,args,kwargs,silent):

    # pull command or sub-command function from exported
    if args[0] in self.exported:
      val = self.exported[args[0]]
      if isinstance(val,dict):
        if len(args)<2:
          if not silent:
            self.output('*** Missing sub-command (%s SUBCMD)' % args[0])
            self.output('***   valid: %s' % ','.join(sorted(list(self.exported_sub))))
            return (None,None)
        elif args[1] in val:
          (func,args) = (val[args[1]],args[2:])
        else:
          if not silent:
            self.output('*** Unknown sub-command "%s"' % args[1])
          return (None, None)
      else:
        (func,args) = (val,args[1:])
    else:
      if not silent:
        self.output('*** Unknown command "%s"' % args[0])
      return (None,None)

    return (func,args)

  # do nothing because we print "Unknown command" elsewhere
  # @param line (str) raw command text
  def default(self,line):
    return

  # add an extra newline after command output so the interface looks better
  # @param stop (bool)
  # @param line (str)
  def postcmd(self,stop,line):

    print('')
    return stop

###############################################################################
# helper functions
###############################################################################

  # @param line (str) a command string
  # @param lower (bool) [False] whether to lowercase everything
  # @param split (bool) [True] turn args containing commas into list objects
  # @return (2-tuple)
  #   #0 (list of (str OR list)) space-separated args
  #   #1 (dict of name:(str OR list)) keyword arguments
  def get_args(self,line,lower=False,split=True):
    """get space-separated args accounting for quotes"""

    args = []
    kwargs = {}
    quote = False
    last_quoted = False
    to_lower = lower
    s = ''
    key = None
    for c in (line.strip()+' '):

      # separate on spaces unless inside quotes
      if c==' ':
        if quote:
          s += c
        elif s:
          if not last_quoted and split and ',' in s:
            s = s.split(',')

          # differentiate args and kwargs
          if key:
            kwargs[key] = s
            key = None
          else:
            args.append(s)

          last_quoted = False
          s = ''

        elif key:
          raise ArgsError(None,'keyword values cannot be blank')

      # keep track of quotes
      elif c=='"':
        if quote:
          quote = False
          to_lower = lower
        else:
          quote = True
          last_quoted = True
          to_lower = False
      
      # keep track of kwargs
      elif c=='=' and not quote:
        if key:
          raise ArgsError(None,'you must quote "=" in keyword values')
        if not s:
          raise ArgsError(None,'keyword names cannot be blank')
        key = s
        s = ''
        last_quoted = False

      # add characters to the current string
      else:
        s += c.lower() if to_lower else c

    return (args,kwargs)

  # compare user-provided args/kwargs to the command signature
  # @param func (func) the function to use as reference
  # @param user_args (list)
  # @param user_kwargs (dict)
  # @param arb (bool) whether the function accepts arbitrary args
  # @raise ArgsError
  def check_args(self,func,user_args,user_kwargs,arb=False):

    (sig,args,kwargs) = self.get_sig(func)
    if len(user_args)<len(args):
      raise ArgsError(sig,'missing required args')
    if arb:
      return

    if len(user_args)>len(args+kwargs):
      raise ArgsError(sig,'too many args')

    for key in user_kwargs:
      if key not in kwargs:
        raise ArgsError(sig,'unknown keyword "%s"' % key)

  # inspect a function and parse out its signature
  # @param func (func)
  # @return (3-tuple)
  #   #0 (str) user-readable signature
  #   #1 (list) argument names
  #   #2 (list) keyword argument names
  def get_sig(self,func):

    (args,varargs,keywords,defaults,_,_,_) = inspect.getfullargspec(func)
    args.remove('self')
    split = -len(defaults) if defaults else 0
    kwargs = args[split:] if split else []
    args = args[:split] if split else args

    # account for sub commands e.g. "set_stat"
    name = func.__name__.replace('_',' ')

    opts = ['[%s]' % s for s in kwargs]
    space = ' ' if args and opts else ''
    sig = '(%s) %s%s%s' % (name,' '.join(args),space,' '.join(opts))

    return (sig,args,kwargs)

  # pull in exported commands and aliases from the Character
  # @param char (Character)
  def plug(self,char):

    self.unplug()
    self.char = char

    # basic commands
    self.exported = {name:getattr(char,name) for name in char.export}

    # sub commands e.g. "set_stat"
    self.exported_sub = set()
    funcs = inspect.getmembers(char,inspect.ismethod)
    for prefix in char.export_prefix:
      self.exported[prefix] = {}
      for (name,func) in funcs:
        if name.startswith(prefix+'_'):
          name = name[len(prefix)+1:]
          self.exported[prefix][name] = func
          self.exported_sub.add(name)

    # basic aliases
    for (alias,target) in char.export_alias.items():
      self.exported[alias] = self.exported[target]

    # sub-command aliases
    for (alias,target) in char.export_sub_alias.items():
      for prefix in char.export_prefix:
        self.exported[prefix][alias] = self.exported[prefix][target]

  def unplug(self):

    self.char = None
    self.exported = {}

  # @param fname (str) the destination file
  # @return (bool) if saving was successful
  def save(self,fname=None):

    if not fname:
      fname = input('Enter a file name: ')
    try:
      with open(fname,'a') as f:
        pass
    except:
      self.output('Unable to write to "%s"' % fname)
      return False

    self.char.save(fname)
    self.fname = fname
    self.modified = False

    return True

  # check if the character has been modified and prompt for save
  # @return (bool) if it's okay to trash our current character data
  def overwrite(self):

    if self.char is None or not self.modified:
      return True
    choice = input('\nSave changes to current char? (Y/n/c): ').lower()
    if choice in ('','y','yes'):
      return self.save()==False
    if choice in ('n','no'):
      return True
    return False

  # add our indent to any inputs
  def input(self, s):

    return input(self.indent + s + ': ').strip()

  # add our indent to any result output
  def output(self, s):

    s = str(s).replace('\n', '\n' + self.indent)
    print(self.indent + s)

  # logging functions
  def log(self, s, level='info'):
    if self.logger:
      getattr(self.logger, level)(s)
  def debug(self, s):
    self.log(s, 'debug')
  def info(self, s):
    self.log(s, 'info')
  def warning(self, s):
    self.log(s, 'warning')
  def error(self, s):
    self.log(s, 'error')
  def critical(self, s):
    self.log(s, 'critical')

  # log exception info
  def log_exc(self, s, trace, level='error'):

    self.log(s, level)
    self.debug(trace)

###############################################################################
# user commands
###############################################################################

  def do_EOF(self,args):
    """exit the CLI (prompts for save)"""

    if self.overwrite():
      return True
  do_exit = do_EOF
  do_quit = do_EOF

  def do_help(self,args):
    """show help text for commands"""

    # find the referenced comand or sub-command
    if args and args[0] in self.exported:
      func = self.exported[args[0]]
      if isinstance(func,dict):
        if len(args)<2 or args[1] not in func:
          self.output('*** Missing sub-command (%s SUBCMD)' % args[0])
          self.output('***   valid: %s' % ','.join(sorted(list(self.exported_sub))))
          return
        func = func[args[1]]

      # first line is always the signature
      print('')
      self.output('# '+self.get_sig(func)[0])

      # look for __doc__ text in the function ot its parent
      if not func.__doc__:
        func = getattr(super(self.char.__class__,self.char),func.__name__)

      # be smart about formatting leading indents and newlines
      if func.__doc__:
        lines = [x.rstrip() for x in func.__doc__.split('\n')]
        while len(lines) and not lines[0].strip():
          del lines[0]
        while len(lines) and not lines[-1].strip():
          del lines[-1]
        leading = len(lines[0])-len(lines[0].lstrip())
        if leading>0:
          lines = [x[leading:] for x in lines]
        self.output('#')
        self.output('\n'.join(['# '+x for x in lines]))
        return

    # if the requested function is defined here rather than in our Character
    # just use the cmd.Cmd built-in help method
    cmd.Cmd.do_help(self,' '.join(args))

    # help text for exported commands
    if not args and self.char is not None:
      self.print_topics(
          'Character commands:',
          sorted(self.char.export),
          15, 80
      )
      self.print_topics(
          'Prefixed cmds:',
          sorted(self.char.export_prefix),
          15, 80
      )

  def do_load(self,args):
    """load a character from a file"""

    if not args:
      self.output('Missing file name')
    elif not self.overwrite():
      return
    elif not os.path.isfile(args[0]):
      self.output('Unable to read "%s"' % args[0])
    else:
      (c, errors) = char.Character.load(args[0],
          logger=self.logger, output_func=self.output, input_func=self.input)

      # print semi-detailed errors to help with debugging
      if errors:
        self.output('Failed to load "%s"' % args[0])
        self.output('\n'.join(errors))
      else:
        self.unplug()
        self.plug(c)
        self.fname = args[0]
        self.modified = False

  def do_save(self,args):
    """save a character to a file"""

    self.save(self.fname if not args else ' '.join(args))

  def do_close(self,args):
    """close the current character (prompts for save)"""

    if not self.char:
      return
    if self.overwrite():
      self.unplug()

  # defaults to a Pathfinder character
  def do_new(self,args):
    """create a new character"""

    if not self.overwrite():
      return
    args = 'Pathfinder' if not args else args[0]

    try:
      c = char.Character.new(args, logger=self.logger,
          input_func=self.input, output_func=self.output)
    except KeyError:
      self.output('Unknown Character type "%s"; known types:' % args)
      self.output('  '+'\n  '.join(sorted(char.Character.get_systems())))
      return

    self.plug(c)
    self.modified = True

  def do_roll(self,args):
    """roll some dice"""

    try:
      self.output(Dice(' '.join(args)).roll())
    except ValueError:
      self.output('*** invalid Dice string "%s"' % ' '.join(args))

###############################################################################
# Dev commands
###############################################################################

  def do_eval(self,args):
    """[DEV] run eval() on input"""

    try:
      self.output(eval(' '.join(args)))
    except:
      self.output(traceback.format_exc())

  def do_exec(self,args):
    """[DEV] run exec() on input"""

    try:
      exec(' '.join(args))
    except:
      self.output(traceback.format_exc())

  def do_trace(self,args):
    """[DEV] print traceback for last exception"""

    self.output(self.last_trace)

  def do_args(self,args):
    """[DEV] toggle printing args"""

    self.debug_flags['args'] = not self.debug_flags['args']
    self.output('Print args: %s' % self.debug_flags['args'])

  def do_nop(self,args):
    """[DEV] do nothing"""

    return

  def old_load(self,args):

    if not args:
      self.output('Missing file name')
      return
    if not self.overwrite():
      return
    if not os.path.isfile(args[0]):
      self.output('Unable to read "%s"' % args[0])
      return
    try:
      with open(args[0],'rb') as f:
        self.plug(pickle.load(f))
        self.fname = args[0]
        self.modified = False
    except Exception:
      self.output('Unable to unpickle "%s"' % args[0])

  def old_save(self,args):

    fname = self.fname if not args else ' '.join(args)
    if not fname:
      fname = input('Enter a file name: ')
    try:
      with open(fname,'a') as f:
        pass
    except:
      self.output('Unable to write "%s"' % fname)
      return
    try:
      with open(fname,'wb') as f:
        pickle.dump(self.char,f,-1)
      self.fname = fname
      self.modified = False
      return False
    except:
      self.output('Unable to pickle character')

###############################################################################
# cli invocation hook
###############################################################################

if __name__=='__main__':

  main(sys.argv[1:])
