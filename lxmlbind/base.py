"""
Declarative object base class.
"""
from functools import partial
from inspect import getmro
from itertools import imap
from logging import getLogger

from lxml import etree

from lxmlbind.property import Property, set_child


class Base(object):
    """
    Base class for objects using LXML object binding.
    """
    def __init__(self, element=None, parent=None, *args, **kwargs):
        """
        :param element: an optional root `lxml.etree` element
        :param parent: an optional parent pointer to another instance of `Base`
        """
        if element is None:
            self._element = self._new_default_element(*args, **kwargs)
        elif element.tag != self.tag():
            raise Exception("'{}' object requires tag '{}', not '{}'".format(self.__class__,
                                                                             self.tag(),
                                                                             element.tag))
        else:
            self._element = element

        self._parent = parent
        self._set_default_properties()

    def _new_default_element(self, *args, **kwargs):
        """
        Generate a new default element for this object.

        Subclasses may override this function to provide more complex default behavior.
        """
        return etree.Element(self.tag())

    def _set_default_properties(self):
        """
        Iterate over properties and populate default values.
        """
        for class_ in getmro(self.__class__):
            for member in class_.__dict__.values():
                if not isinstance(member, Property) or not member.auto:
                    continue
                if member.__get__(self, self.__class__) is None:
                    member.__set__(self, member.default)

    @classmethod
    def tag(cls):
        """
        Defines the expected tag of the root element of object's of this class.

        The default behavior is to use the class name with a leading lower case.

        For PEP8 compatible class names, this gives a lowerCamelCase name.
        """
        return cls.__name__[0].lower() + cls.__name__[1:]

    def to_xml(self, pretty_print=False):
        """
        Encode as XML string.
        """
        return etree.tostring(self._element, pretty_print=pretty_print)

    @classmethod
    def from_xml(cls, xml):
        """
        Decode from an XML string.
        """
        return cls(etree.XML(bytes(xml)))

    def search(self,
               tags,
               element=None,
               create=False,
               set_attributes=None,
               logger=getLogger("lxmlbind.base")):
        """
        Search `lxml.etree` rooted at `element` for the first child
        matching a sequence of element tags.

        :param tags: the list of tags to traverse
        :param element: the root element of the tree or None to use this object's element
        :param create: optionally, create the element path while traversing
        :param attributes: optional attributes dictionary to set in the leaf element, when created
        """
        head, tail = tags[0], tags[1:]
        parent = self._element if element is None else element
        child = parent.find(head)
        if child is None:
            if create:
                logger.debug("Creating element '{}' for '{}'".format(head, parent.tag))
                child = etree.SubElement(parent, head)
                if set_attributes is not None and not tail:
                    set_attributes(child, self)
            else:
                return None
        return self.search(tail, child, create) if tail else child

    def __str__(self):
        """
        Return XML string.
        """
        return self.to_xml()

    def __hash__(self):
        """
        Hash using XML element.
        """
        return self._element.__hash__()

    def __eq__(self, other):
        """
        Compare using XML element equality, ignoring whitespace differences.
        """
        if other is None:
            return False
        return eq_xml(self._element, other._element)

    def __ne__(self, other):
        """
        Compare using XML element equality, ignoring whitespace differences.
        """
        return not self.__eq__(other)

    @classmethod
    def property(cls, path=None, default=None, **kwargs):
        """
        Generate a property that matches this class.
        """
        return Property(cls.tag() if path is None else path,
                        get_func=cls,
                        set_func=set_child,
                        auto=True,
                        default=default,
                        **kwargs)


class List(Base):
    """
    Extension that supports treating elements as list of other types.
    """
    @classmethod
    def of(cls):
        """
        Defines what this class is a list of.

        :returns: a function that operates on `lxml.etree` elements, returning instances of `Base`.
        """
        return Base

    def _of(self):
        # bind parent
        return partial(self.of(), parent=self)

    def append(self, value):
        self._element.append(value._element)
        value._parent = self

    def __getitem__(self, key):
        item = self._of()(self._element.__getitem__(key))
        return item

    def __setitem__(self, key, value):
        self._element.__setitem__(key, value._element)
        value._parent = self

    def __delitem__(self, key):
        # Without keeping a parallel list of Base instances, it's not
        # possible to detach the _parent pointer of values added via
        # append() or __setitem__. So far, not keeping a parallel list
        # is worth it.
        self._element.__delitem__(key)

    def __iter__(self):
        return imap(self._of(), self._element.__iter__())

    def __len__(self):
        return len(self._element)


def tag(name):
    """
    Class decorator that replaces `Base.tag()` with a function that returns `name`.
    """
    def wrapper(cls):
        if not issubclass(cls, Base):
            raise Exception("lxmlbind.base.tag decorator should only be used with subclasses of lxmlbind.base.Base")

        @classmethod
        def tag(cls):
            return name

        cls.tag = tag
        return cls
    return wrapper


def of(child_type):
    """
    Class decorator that replaces `List.of()` with a function that returns `child_type`.
    """
    def wrapper(cls):
        if not issubclass(cls, Base):
            raise Exception("lxmlbind.base.of decorator should only be used with subclasses of lxmlbind.base.Base")

        @classmethod
        def of(cls):
            return child_type

        cls.of = of
        return cls
    return wrapper


def eq_xml(this,
           that,
           ignore_attributes=None,
           ignore_whitespace=True,
           logger=getLogger("lxmlbind.base")):
    """
    XML comparison on `lxml.etree` elements.

    :param this: an `lxml.etree` element
    :param that: an `lxml.etree` element
    :param ignore_attributes: an optional list of attributes to ignore
    :param ignore_whitespace: whether whitespace should matter
    """
    ignore_attributes = ignore_attributes or []

    # compare tags
    if this.tag != that.tag:
        if logger is not None:
            logger.debug("Element tags do not match: {} != {}".format(this.tag, that.tag))
        return False

    # compare attributes
    def _get_attributes(attributes):
        return {key: value for key, value in attributes.iteritems() if key not in ignore_attributes}

    these_attributes = _get_attributes(this.attrib)
    those_attributes = _get_attributes(that.attrib)
    if these_attributes != those_attributes:
        if logger is not None:
            logger.debug("Element attributes do not match: {} != {}".format(these_attributes,
                                                                            those_attributes))
        return False

    # compare text
    def _strip(tail):
        if tail is None:
            return None
        return tail.strip() or None

    this_text = _strip(this.text) if ignore_whitespace else this.text
    that_text = _strip(that.text) if ignore_whitespace else that.text

    if this_text != that_text:
        if logger is not None:
            logger.debug("Element text does not match: {} != {}".format(this_text,
                                                                        that_text))
        return False

    this_tail = _strip(this.tail) if ignore_whitespace else this.tail
    that_tail = _strip(that.tail) if ignore_whitespace else that.tail

    if this_tail != that_tail:
        if logger is not None:
            logger.debug("Element tails do not match: {} != {}".format(this_tail,
                                                                       that_tail))
        return False

    # evaluate children
    these_children = sorted(this.getchildren(), key=lambda element: element.tag)
    those_children = sorted(that.getchildren(), key=lambda element: element.tag)
    if len(these_children) != len(those_children):
        if logger is not None:
            logger.debug("Element children length does not match: {} != {}".format(len(these_children),
                                                                                   len(those_children)))
        return False

    # recurse
    for this_child, that_child in zip(these_children, those_children):
        if not eq_xml(this_child, that_child, ignore_attributes, ignore_whitespace):
            return False
    else:
        return True
