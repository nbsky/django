# encoding: utf-8
class AttributeSetter(object):
    def __init__(self, name, value):
        setattr(self, name, value)
