# Field hierarchy:
#   Stat
#     PathfinderSkill
#   Bonus
#   Effect
#   Duration
#   Item
#     Weapon
#     Armor
#   Dice
#   Ability
#     Spell
#   Event
#   Text

# [TODO] finish Effects (duration tracking, etc.)
# [TODO] Item
# [TODO] Weapon
# [TODO] Armor
# [TODO] Ability
# [TODO] Spell
# [TODO] Event

import time
from collections import OrderedDict
from functools import reduce

from dnd.char_sheet.errors import *

###############################################################################
# Field class
#   - parent class for objects used by Characters
#   - setting the FIELDS dict enables saving and loading in child classes
#   - child classes should consider overriding everything except load/save
###############################################################################

class Field(object):

  # this should be an OrderedDict of name:type where typ is one of:
  #   str - no action taken
  #   None - we will always pass None ignoring actual content
  #   list - splits on commas
  #   bool - compares against the string 'True'
  FIELDS = {}

  # parse an argument list into a (sub-classed) Field object
  # @param fields (list of str) arguments to pass to the object __init__
  # @return (object) an instance a Field sub-class
  @classmethod
  def load(cls,fields):

    parsed = []
    i = 0
    for typ in cls.FIELDS.values():
      val = None
      if typ is not None:
        if typ is list:
          val = fields[i].split(',')
        elif typ is bool:
          val = fields[i]=='True'
        else:
          val = typ(fields[i])
        i += 1
      parsed.append(val)
    return cls(*parsed)

  # calls str() on each field (or on each item if the field is a list)
  # @return (list of str) fields to save
  def save(self):

    result = []
    for (field,typ) in self.FIELDS.items():
      if typ is None:
        continue
      val = getattr(self,field)
      if isinstance(val,list):
        result.append(','.join([str(x) for x in val]))
      else:
        result.append(str(val))
    return result

  # create variables and establish dependencies in a Character
  # @param (Character) the character to plug into
  def plug(self,char):
    pass

  # try to remove ourself from the Character, checking for dependency issues
  # @raise DependencyError if other Fields depend on us
  def unplug(self):
    pass

  # recalculate our value
  def calc(self):
    pass

  # what to print for the "search" command
  def str_search(self):
    return str(self)

  # what to print for the "all" command
  def str_all(self):
    return str(self)

  # what to print in other cases, including the "get" command
  def __str__(self):
    return repr(self)

  # this gets called in the interpreter, and for when in a collection
  def __repr__(self):
    return '<%s %s>' % (self.__class__.__name__,self.name)

###############################################################################
# Stat class
#   - stat tracking and calculation via string formulas and eval()
#   - stats are considered "root" nodes if their formula is static
#   - or "leaf" nodes if no other stat depends on it
#   - Example: dexterity > dex > _ac_dex > ac
#     - roots: dexterity
#     - leaves: ac
#     - neither: dex, _ac_dex
#   - stats whose name begins with '_' are protected by default
#   - stats can have Bonuses that affect their value
###############################################################################

class Stat(Field):

  FIELDS = OrderedDict([
      ('name',str),
      ('original',str),
      ('text',str),
      ('bonuses',None),
      ('protected',bool),
      ('updated',float)
  ])

  VARS = {'$':'self.char.stats["%s"].value',
      '#':'self.char.stats["%s"].normal',
  }

  # @param name (str)
  # @param formula (str) ['0'] will get passed to eval()
  #   using $NAME refers to the value of the Stat by that name
  #   using #NAME refers to the normal (no bonuses) value
  #   using @NAME refers to an attribute of this Stat object
  #   these can be wrapped in braces to prevent conflicts e.g. ${NAME}
  # @param text (str) ['']
  # @param bonuses (Bonus,list of Bonus) [None] bonuses affecting this stat
  # @param protected (bool) [name.startswith('_')] if this stat is protected
  # @param updated (float) [time.time()] when this Stat was updated
  def __init__(self,name,formula='0',text='',bonuses=None,protected=None,
      updated=None):

    self.char = None
    self.name = name
    self.text = text

    # during plug() in self.formula we replace aliases with valid python code
    # when displaying to the user or creating a new Stat we need the original
    self.formula = str(formula)
    self.original = self.formula

    # this is a dict of type:list where the elements of the list are Bonus
    # e.g. {"armor":[<Bonus mage_armor>,<Bonus shield>]}
    self.bonuses = bonuses or {}
    self.protected = name.startswith('_') if protected is None else protected
    self.updated = time.time() if updated is None else updated

    self.uses = set()
    self.usedby = set()
    self.normal = None
    self.value = None
    self.root = True
    self.leaf = True

    # overridden in sub-classes to specify additional fields to copy()
    self.COPY = []

  # @raise FormulaError
  def plug(self,char):

    self.char = char

    # iterate over each stat in the character and replace matching #/$ aliases
    s = self.formula
    usedby = set()
    for name in char.stats:
      for (var,expand) in self.VARS.items():
        orig = s
        s = s.replace(var+name,expand % name)
        s = s.replace('%s{%s}' % (var,name),expand % name)
        if s!=orig:
          self.uses.add(name)
          self.root = False
          usedby.add(char.stats[name])

    # iterate over our attributes and replace matching @ aliases
    for name in dir(self):
      s = s.replace('@'+name,'self.'+name)
      s = s.replace('@{'+name+'}','self.'+name)

    # if any aliases were invalid or misspelled, we'll have "#NAME" left which
    # will throw an exception in the eval()
    # of course can also throw syntax errors if something else is wrong
    try:
      eval(s)
    except Exception as e:
      raise FormulaError('%s in "%s"' % (e.__class__.__name__,s))

    # add ourselves as a dependant to stats that are in our formula
    for stat in usedby:
      stat.usedby.add(self.name)
      stat.leaf = False

    self.formula = s
    self.calc()

  # remove this stat from its character if possible
  # @param force (bool) [False] ignore dependency issues for this stat
  # @param recursive (bool) [False] remove all our dependants as well
  # @raise RuntimeError if we don't have a character
  # @raise DependencyError
  def unplug(self,force=False,recursive=False):

    if not self.char:
      raise RuntimeError('plug() must be called before unplug()')

    if self.usedby and not force and not recursive:
      raise DependencyError('still usedby: '+','.join(self.usedby))

    # unplug our dependants if requested
    if recursive:
      for name in self.usedby:
        stat = self.char.stats[name]
        stat.unplug(recursive=recursive)
    self.usedby = set()

    self.formula = self.original

    for name in self.uses:
      stat = self.char.stats[name]
      stat.usedby.remove(self.name)
      if not stat.usedby:
        stat.leaf = True
    self.uses = set()

    self.root = True
    self.leaf = True
    self.char = None

  # convenience method that sets self.formula and self.original
  # @param s (str) formula
  # @raise RuntimeError if we're already plugged in to a character
  def set_formula(self,s):

    if self.char:
      raise RuntimeError('set_formula() must be called before plug()')

    self.formula = s
    self.original = s

  # @raise RuntimeError if we don't have a character
  def calc(self):

    if not self.char:
      raise RuntimeError('plug() must be called before calc()')

    # evaluate our formula without bonuses
    old_v = self.value
    old_n = self.normal
    self.normal = eval(self.formula.replace('.value','.normal'))

    # evaluate our formula with bonuses
    self.value = eval(self.formula)
    for (typ,bonuses) in self.bonuses.items():
      bonuses = [b.get_value() for b in bonuses if b.active]
      if not bonuses:
        continue
      if self.char._stacks(typ):
        self.value += sum(bonuses)
      else:
        self.value += max(bonuses)

    # if we changed, bubble the calc() up through our dependants
    if old_v!=self.value or old_n!=self.normal:
      for stat in self.usedby:
        stat = self.char.stats[stat]
        stat.calc()

  # add a bonus to this stat that will affect its value
  # @param bonus (Bonus) the Bonus to add
  def add_bonus(self,bonus):

    typ = bonus.typ
    if typ in self.bonuses:
      self.bonuses[typ].append(bonus)
    else:
      self.bonuses[typ] = [bonus]

  # remove a bonus from this stat
  # @param bonus (Bonus) the Bonus to remove
  # [TODO] raise a KeyError or return a bool?
  def del_bonus(self,bonus):

    typ = bonus.typ
    self.bonuses[typ] = [b for b in self.bonuses[typ] if b is not bonus]
    if not self.bonuses[typ]:
      del self.bonuses[typ]

  # return all bonuses that can affect this stat, including from dependencies
  # @return (2-tuple)
  #   #0 (list of Bonus) permanent bonuses
  #   #1 (list of Bonus) conditional bonuses
  def get_bonuses(self):

    bonuses = []
    conds = []
    for typ in self.bonuses.values():
      for b in typ:
        if b.condition:
          conds.append((self.name,b))
        else:
          bonuses.append((self.name,b))

    # recurse over dependencies
    for stat in self.uses:
      (b,c) = self.char.stats[stat].get_bonuses()
      bonuses += b
      conds += c

    return (bonuses,conds)

  # copy this Stat into a new object with specified changes
  # @param kwargs (dict) fields to update
  # @return (Stat) the copy
  def copy(self,**kwargs):

    a = []
    for var in ('name','text','bonuses','updated'):
      a.append(kwargs.get(var,getattr(self,var)))
    formula = kwargs.get('formula',self.original)
    a.insert(1,formula)

    # sub-classes of Stat can specify additional fields to copy
    k = {}
    for var in self.COPY:
      k[var] = kwargs.get(var,getattr(self,var))

    return self.__class__(*a,**k)

  # looks like: rl
  #   r (root) our formula has no dependencies
  #   l (leaf) no other stat depends on us
  # @return (str)
  def _str_flags(self):

    root = '-r'[self.root]
    leaf = '-l'[self.leaf]
    return '%s%s' % (root,leaf)

  # looks like: rl 999 NAME (b:5/10 ?:0/5)
  # followed by conditional bonuses indented on new lines
  # @param cond_bonuses (bool) [False] whether to print conditional bonuses
  # @return (str)
  def _str(self,cond_bonuses=False):

    flags = self._str_flags()
    (bonuses,conds) = self.get_bonuses()
    (total_b,total_c,active_b,active_c) = (len(bonuses),len(conds),0,0)
    for (stat,b) in bonuses:
      if b.active:
        active_b += 1
    for (stat,b) in conds:
      if b.active:
        active_c += 1
    stats = 'b:%s/%s ?:%s/%s' % (active_b,total_b,active_c,total_c)
    bons = ''
    if cond_bonuses:
      bons = ''.join(['\n  %s'%b[1]._str(stat=False) for b in conds])
    return '%s %3s %s (%s)%s' % (flags,self.value,self.name,stats,bons)

  # don't print conditional bonuses
  # @return (str)
  def str_search(self):
    return self._str()

  # do print conditional bonuses
  # @return (str)
  def __str__(self):
    return self._str(True)

  # @return (str)
  def str_all(self):

    l =     ['  value | %s' % self.value]
    l.append('formula | %s' % self.original)
    x = []

    # each bonus gets its own line
    # if the bonus is on one of our dependencies, include its name
    (bonuses,conds) = self.get_bonuses()
    for (stat,bonus) in bonuses:
      name = '' if stat==self.name else '<%s> ' % stat
      x +=  ['  bonus | %s%s' % (name,bonus._str(stat=False))]
    l.extend(sorted(x))
    x = []
    for (stat,bonus) in conds:
      name = '' if stat==self.name else '<%s> ' % stat
      x +=  [' bonus? | %s%s' % (name,bonus._str(stat=False))]
    l.extend(sorted(x))

    l.append(' normal | %s' % self.normal)
    l.append('   uses | %s' % ','.join(sorted(self.uses)))
    l.append('used by | %s' % ','.join(sorted(self.usedby)))
    l.append('   text | %s' % self.text)
    return '\n'.join(l)

###############################################################################
# Bonus class
#   - bonuses add a number (or Dice) to a stat or item
#   - they can be turned on and off
#   - they can be conditional
###############################################################################

class Bonus(Field):

  FIELDS = OrderedDict([
      ('name',str),
      ('value',int),
      ('stats',list),
      ('typ',str),
      ('condition',str),
      ('text',str),
      ('active',bool)
  ])

  # @param name (str)
  # @param value (int,Dice) the value to add to our stats
  # @param stats (str,list of str) names of stats that we affect
  # @param typ (str) ['none'] our type e.g. armor, dodge, morale
  # @param cond (str) [''] when this bonus applies if not all the time
  # @param text (str) ['']
  # @param active (bool) [not cond] whether we're "on" and modifying stats
  def __init__(self,name,value,stats,typ=None,cond=None,text=None,active=True):

    self.name = name
    self.value = value
    self.stats = stats if isinstance(stats,list) else [stats]
    self.text = text or ''
    self.active = False if cond else active
    self.typ = (typ or 'none').lower()
    self.condition = cond or ''

    self.char = None
    self.usedby = set() # this will contain Effects
    self.last = active # remember the state we're in before a toggle

  # @param char (Character)
  # @raise ValueError if our type isn't in our Character
  def plug(self,char):

    # I'd rather have this in __init__ but we don't have a char at that point
    if char.BONUS_TYPES and self.typ not in char.BONUS_TYPES:
      raise ValueError('invalid bonus type "%s"' % self.typ)

    for name in self.stats:
      stat = char.stats[name]
      stat.add_bonus(self)
      stat.calc()
    self.char = char

    # some bonuses should never be turned off
    if not self.condition and self.typ in self.char.BONUS_PERM:
      self.active = True

  def unplug(self):

    if not self.char:
      raise RuntimeError('plug() must be called before unplug()')

    for name in self.stats:
      stat = self.char.stats[name]
      stat.del_bonus(self)
      stat.calc()
    self.char = None

  # @return (int) the value of this bonus
  def get_value(self):
    return self.value

  def calc(self):

    for name in self.stats:
      self.char.stats[name].calc()

  def on(self):
    self.toggle(True)

  # @param force (bool) [False] turn off even if we belong to an active Effect
  def off(self,force=False):

    # if we belong to an Effect and it is active, we should stay on
    if force or not reduce(lambda a,b:a or b.is_active(),self.usedby,False):
      self.toggle(False)

  # @param new (bool) the new state; could be the same as our old state
  def toggle(self,new):

    # some bonuses should never be turned off
    if not self.condition and self.typ in self.char.BONUS_PERM:
      return
    self.last = self.active
    self.active = new
    self.calc()

  # this is planned to be used for the "with" conditional which hasn't been
  # implemented yet e.g. "get ac with mage_armor" / "get ac without mage_armor"
  def revert(self):

    if self.active==self.last:
      return
    self.active = self.last
    self.calc()

  # looks like: [+] NAME VALUE STATS (type)
  # conditional: [-] NAME VALUE STATS (type) ? CONDITION
  # @param name (bool) [True] print our name
  # @param stat (bool) [True] print the stats we affect
  # @return (str)
  def _str(self,name=True,stat=True):

    (n,s) = ('','')
    if name:
      n = ' %s' % self.name
    if stat:
      s = ' %s' % ','.join(self.stats)
    act = '-+'[self.active]
    sign = '+' if self.value>=0 else ''
    cond = '' if not self.condition else ' ? %s' % self.condition
    return '[%s]%s %s%s%s (%s)%s' % (act,n,sign,self.get_value(),s,self.typ,cond)

  # @return (str)
  def __str__(self):
    return self._str()

  # @return (str)
  def str_all(self):

    l =     ['  value | %s' % self.get_value()]
    l.append(' active | %s' % self.active)
    l.append('   type | %s' % self.typ)
    l.append(' revert | %s' % ('change','same')[self.last==self.active])
    l.append('  stats | %s' % ','.join(sorted(self.stats)))
    l.append('conditn | %s' % self.condition)
    l.append('   text | %s' % self.text)
    return '\n'.join(l)

###############################################################################
# Duration class
#   - tracks a duration e.g. 1 round, 2 hours, 5 days
#   - can also be infinite
#   - can be based on $level or $caster_level e.g. 1/CL 1mi/2CL
###############################################################################

class Duration(object):

  INF = -1
  INF_NAMES = (None,'inf','infinity','infinite','perm','permanent','forever')

  UNITS = {
      ('','r','rd','rds','rnd','rnds','round','rounds') : 1,
      ('m','mi','min','mins','minute','minutes') : 10,
      ('h','hr','hrs','hour','hours') : 600,
      ('d','day','days') : 14400,
      ('y','yr','yrs','year','years') : 5259600,
      ('l','lvl','level') : '$level',
      ('cl','clvl','caster','casterlvl','casterlevel') : '$caster_level'
  }

  NAMES = [(5259600,'yr'),(14400,'day'),(600,'hr'),(10,'min'),(1,'rd')]

  # @param s (str)
  # @return (bool) whether the input string is an int
  @staticmethod
  def is_int(s):

    try:
      int(s)
      return True
    except:
      return False

  # @param s (str) unit name
  # @return (int) the corresponding multiplier for conversion to rounds
  @staticmethod
  def get_mult(s):

    for (names,mult) in Duration.UNITS.items():
      if s in names:
        return mult

    raise KeyError('unknown unit "%s"' % s)

  # @param s (str) one duration text
  # @return (2-tuple)
  #   #0 (str) the number
  #   #1 (str) unit
  @staticmethod
  def split_unit(s):

    i = 0
    while i<len(s) and Duration.is_int(s[i]):
      i += 1
    return (s[:i] or '1',s[i:])

  # @param s (str) full duration e.g. 1+1/CL
  # @return (str) input converted to valid python expression to be eval()
  @staticmethod
  def to_rds(s):

    if isinstance(s,int):
      return str(s)

    if isinstance(s,str):
      s = s.lower().replace(' ','').replace('_','')
    if s in Duration.INF_NAMES:
      return str(Duration.INF)

    durs = s.split('+')

    rds = []
    for dur in durs:

      if dur.count('/')>1:
        raise ValueError('too many / in "%s"' % dur)

      dur = dur.split('/')
      (num,unit) = Duration.split_unit(dur[0])
      s = '%s*%s' % (num,Duration.get_mult(unit))

      if len(dur)>1:
        (num,stat) = Duration.split_unit(dur[1])
        s += '*max(1,int(%s/%s))' % (Duration.get_mult(stat),num)

      rds.append(s)

    return '+'.join(rds)

  # @param s (str) text to parse
  # @param char (Character)
  # @return (int) number of rounds
  # @raise ValueError if we need a Character but don't have one
  @staticmethod
  def parse(s,char):

    s = Duration.to_rds(s)

    # expand $level and $caster_level
    for unit in Duration.UNITS.values():
      if isinstance(unit,str) and unit.startswith('$'):
        s = s.replace(unit,'char.stats["%s"].value' % unit[1:])
    if 'char.stats' in s and not char:
      raise ValueError('string references Stats but missing Character')

    return (s,eval(s))

  # @param dur (str) [None] the duration text to parse (None = infinite)
  # @param char (Character) [None]
  def __init__(self,dur=None,char=None):

    (self.raw,self.original) = Duration.parse(dur,char)
    self.rounds = self.original

  # @param dur (int,Duration) [1] value to subtract from remaining time
  # @return (bool) True if this Duration has expired
  # @raise TypeErrpr on dur
  def advance(self,dur=1):

    if isinstance(dur,Duration):
      dur = dur.rounds
    elif not isinstance(dur,int):
      raise TypeError('invalid type "%s"' % dur.__class__.__name__)

    if self.rounds==Duration.INF:
      return False
    self.rounds = max(0,self.rounds-dur)
    return self.expired()

  # @return (bool) if we're expired
  def expired(self):

    return self.rounds==0

  def reset(self):

    self.rounds = self.original

  # decompose into sum of years, days, hours, minutes, rounds
  # @return (str)
  def __str__(self):

    if self.rounds==Duration.INF:
      return 'infinite'

    s = []
    x = self.rounds
    for (num,name) in Duration.NAMES:
      if num<x:
        s.append('%s%s' % (x/num,name))
        x = x%num
    return '+'.join(s)

###############################################################################
# Effect class
#   - links a Duration to one or more bonuses
#   - the same Bonus object can be used by multiple Effects
#   - turning an Effect on/off intelligently turns on/off its Bonuses
###############################################################################

class Effect(Field):

  # @param name (str)
  # @param bonuses (str,list of str) bonuses conferred by this Effect
  # @param duration (Duration) [None] defaults to infinite
  # @param text (str) [None]
  # @param active (None,bool) whether this Effect is active
  #   if bool, our bonuses will get toggled; if None they won't
  #
  # @rase TypeError on duration
  def __init__(self,name,bonuses,duration=None,text=None,active=None):

    self.name = name
    self.bonuses = bonuses if isinstance(bonuses,list) else [bonuses]
    self.duration = duration or Duration()
    self.text = text
    self.active = active

    self.last = active
    self.char = None

    if not isinstance(self.duration,Duration):
      raise TypeError('duration must be Duration not "%s"'
          % self.duration.__class__.__name__)

  # @param char (Character)
  def plug(self,char):

    for name in self.bonuses:
      bonus = char.bonuses[name]
      bonus.usedby.add(self.name)
      if self.active is not None:
        if self.active:
          bonus.on()
        else:
          bonus.off()
    self.char = char

  # @raise RuntimeError if plug() wasn't called first
  def unplug(self):

    if not self.char:
      raise RuntimeError('plug() must be called before unplug()')

    for name in self.bonuses:
      bonus = char.bonuses[name]
      bonus.usedby.remove(self.name)
      bonus.off()
    self.char = None

  # @return (bool) if not manually set, tracks duration
  def is_active(self):

    if self.active is not None:
      return self.active
    return not self.duration.expired()

  def on(self):
    self.toggle(True)

  def off(self):
    self.toggle(False)

  # @param new (bool)
  def toggle(self,new):

    self.last = self.active
    self.active = new
    for name in self.bonuses:
      self.char.bonuses[name].toggle(new)

  # see Bonus.revert()
  def revert(self):

    self.active = self.last
    for name in self.bonuses:
      self.char.bonuses[name].revert()

  # looks like: {+} NAME DURATION BONUSES
  # can also start with {-} or {?}
  # @return (str)
  def __str__(self):

    actives = [b.active for b in self.bonuses]
    if all(actives):
      act = '+'
    elif any(actives):
      act = '?'
    else:
      act = '-'
    names = [b.name for b in self.bonuses]
    return '{%s} %s %s (%s)' % (act,self.name,self.duration,','.join(names))

  # @return (str)
  def str_all(self):

    l =      ['duration | %s' % self.duration]
    l.extend([' bonuses | %s' % b for b in self.bonuses])
    l.append( '  active | %s/%s (init: %s)' % (
        len([b for b in self.bonuses if b.active]),
        len(self.bonuses),
        {True:'+',False:'-',None:'='}[self.active]
    ))
    l.append( '    text | '+self.text)
    return '\n'.join(l)

###############################################################################
# Text class
#   - supports newlines via the 2 literal characters '\n'
###############################################################################

class Text(Field):

  FIELDS = OrderedDict([
      ('name',str),
      ('text',str),
  ])

  # @param name (str)
  # @param text (str)
  def __init__(self,name,text):

    self.name = name
    self.set(text)

  # @param text (str)
  def set(self,text):

    # we store newlines internally as '\' + 'n' for ease of saving
    text = text or ''
    self.text = text.strip().replace('\n','\\n')

  # @return (str) truncated to 50 characters and replacing newlines with '|'
  def __str__(self):

    text = '[BLANK]' if not self.text else self.text.replace('\\n',' | ')
    ellip = ''
    if len(text)>50:
      (text,ellip) = (text[:50],'...')
    return '%s: %s%s' % (self.name,text,ellip)

  # @return (str) full text with real newlines
  def str_all(self):

    text = '[BLANK]' if not self.text else self.text
    return '--- %s\n%s' % (self.name,text.replace('\\n','\n'))

###############################################################################
# Event class
#   - WIP
###############################################################################

class Event(object):

  # hp<=0, nonlethal>=hp

  def __init__(self):
    raise NotImplementedError