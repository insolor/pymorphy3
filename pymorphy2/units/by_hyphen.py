# -*- coding: utf-8 -*-
"""
Analyzer units for unknown words with hyphens
---------------------------------------------
"""

from __future__ import absolute_import, unicode_literals, division

from pymorphy2.units.base import BaseAnalyzerUnit, AnalogyAnalizerUnit
from pymorphy2.units.utils import (add_parse_if_not_seen, add_tag_if_not_seen,
                                   with_suffix, without_fixed_suffix,
                                   with_prefix, without_fixed_prefix,
                                   replace_methods_stack)


class HyphenSeparatedParticleAnalyzer(AnalogyAnalizerUnit):
    """
    Parse the word by analyzing it without
    a particle after a hyphen.

    Example: смотри-ка -> смотри + "-ка".

    .. note::

        This analyzer doesn't remove particles from the result
        so for normalization you may need to handle
        particles at tokenization level.

    """
    terminal = True
    ESTIMATE_DECAY = 0.9

    # XXX: maybe the code can be made faster by compiling this list to a DAWG?
    PARTICLES_AFTER_HYPHEN = [
        "-то", "-ка", "-таки", "-де", "-тко", "-тка", "-с", "-ста"
    ]

    def parse(self, word, seen_parses):

        result = []
        for unsuffixed_word, particle in self.possible_splits(word):
            method = (self, particle)

            for fixed_word, tag, normal_form, estimate, methods_stack in self.morph.parse(unsuffixed_word):
                parse = (
                    fixed_word+particle,
                    tag,
                    normal_form+particle,
                    estimate*self.ESTIMATE_DECAY,
                    methods_stack+(method,)
                )
                add_parse_if_not_seen(parse, result, seen_parses)

            # If a word ends with with one of the particles,
            # it can't ends with an another.
            break

        return result

    def tag(self, word, seen_tags):
        result = []
        for unsuffixed_word, particle in self.possible_splits(word):
            result.extend(self.morph.tag(unsuffixed_word))
            # If a word ends with with one of the particles,
            # it can't ends with an another.
            break

        return result

    def possible_splits(self, word):
        if '-' not in word:
            return

        for particle in self.PARTICLES_AFTER_HYPHEN:
            if not word.endswith(particle):
                continue

            unsuffixed_word = word[:-len(particle)]
            if not unsuffixed_word:
                continue

            yield unsuffixed_word, particle

    def normalizer(self, form, this_method):
        particle = this_method[1]
        normal_form = yield without_fixed_suffix(form, len(particle))
        yield with_suffix(normal_form, particle)

    def lexemizer(self, form, this_method):
        particle = this_method[1]
        lexeme = yield without_fixed_suffix(form, len(particle))
        yield [with_suffix(f, particle) for f in lexeme]



class HyphenatedWordsAnalyzer(BaseAnalyzerUnit):
    """
    Parse the word by parsing its hyphen-separated parts.

    Examples:

        * интернет-магазин -> "интернет-" + магазин
        * человек-гора -> человек + гора

    """

    terminal = True
    ESTIMATE_DECAY = 0.75

    _CONSIDER_THE_SAME = {'V-oy': 'V-ey'}

    def __init__(self, morph):
        super(HyphenatedWordsAnalyzer, self).__init__(morph)
        Tag = morph.TagClass
        self._FEATURE_GRAMMEMES = (Tag.PARTS_OF_SPEECH | Tag.NUMBERS |
                                   Tag.CASES | Tag.PERSONS | Tag.TENSES)

    def parse(self, word, seen_parses):
        if not self._should_parse(word):
            return []

        left, right = word.split('-')
        left_parses = self.morph.parse(left)
        right_parses = self.morph.parse(right)

        result = self._parse_as_variable_both(left_parses, right_parses, seen_parses)

        # We copy `seen_parses` to preserve parses even if similar parses
        # were observed at previous step (they may have different lexemes).
        _seen = seen_parses.copy()
        result.extend(self._parse_as_fixed_left(right_parses, _seen, left))
        seen_parses.update(_seen)

        return result

    def _parse_as_fixed_left(self, right_parses, seen, left):
        """
        Step 1: Assume that the left part is an uninflected prefix.
        Examples: интернет-магазин, воздушно-капельный
        """
        result = []

        for fixed_word, tag, normal_form, estimate, right_methods in right_parses:

            if tag._is_unknown():
                continue

            new_methods_stack = ((self, left, right_methods),)

            parse = (
                '-'.join((left, fixed_word)),
                tag,
                '-'.join((left, normal_form)),
                estimate * self.ESTIMATE_DECAY,
                new_methods_stack
            )
            result.append(parse)
            # add_parse_if_not_seen(parse, result, seen_left)

        return result

    def _parse_as_variable_both(self, left_parses, right_parses, seen):
        """
        Step 2: if left and right can be parsed the same way,
        then it may be the case that both parts should be inflected.
        Examples: человек-гора, команд-участниц, компания-производитель
        """
        result = []
        right_features = [self._similarity_features(p[1]) for p in right_parses]

        # FIXME: quadratic algorithm
        for left_parse in left_parses:

            left_tag = left_parse[1]

            if left_tag._is_unknown():
                continue

            left_feat = self._similarity_features(left_tag)

            for parse_index, right_parse in enumerate(right_parses):

                right_feat = right_features[parse_index]

                if left_feat != right_feat:
                    continue

                left_methods = left_parse[4]
                right_methods = right_parse[4]

                new_methods_stack = ((self, left_methods, right_methods),)

                # tag
                parse = (
                    '-'.join((left_parse[0], right_parse[0])),  # word
                    left_tag,
                    '-'.join((left_parse[2], right_parse[2])),  # normal form
                    left_parse[3] * self.ESTIMATE_DECAY,
                    new_methods_stack
                )
                result.append(parse)
                # add_parse_if_not_seen(parse, result, seen_right)

        return result

    def _similarity_features(self, tag):
        """ :type tag: pymorphy2.tagset.OpencorporaTag """
        return tag.grammemes & self._FEATURE_GRAMMEMES

    def tag(self, word, seen_tags):
        result = []
        # TODO: do not use self.parse
        for p in self.parse(word, set()):
            add_tag_if_not_seen(p[1], result, seen_tags)
        return result

    def _should_parse(self, word):
        if '-' not in word:
            return False

        if word.strip('-').count('-') != 1:
            # require exactly 1 hyphen, in the middle of the word
            return False

        if self.dict.prediction_prefixes.prefixes(word):
            # such words should really be parsed by KnownPrefixAnalyzer
            return False

        return True

    def normalized(self, form):
        return next(self._iter_lexeme(form))

    def get_lexeme(self, form):
        return list(self._iter_lexeme(form))

    def _iter_lexeme(self, form):
        methods_stack = form[4]
        assert len(methods_stack) == 1

        this_method, left_methods, right_methods = methods_stack[0]
        assert this_method is self

        if self._fixed_left_method_was_used(left_methods):
            # Form is obtained by parsing right part,
            # assuming that left part is an uninflected prefix.
            # Lexeme can be calculated from the right part in this case:
            prefix = left_methods + '-'

            right_form = without_fixed_prefix(
                replace_methods_stack(form, right_methods),
                len(prefix)
            )
            base_analyzer = right_methods[-1][0]

            lexeme = base_analyzer.get_lexeme(right_form)
            return (
                replace_methods_stack(
                    with_prefix(f, prefix),
                    ((this_method, left_methods, f[4]),)
                )
                for f in lexeme
            )

        else:
            # Form is obtained by parsing both parts.
            # Compute lexemes for left and right parts,
            # then merge them.
            left_form = self._without_right_part(
                replace_methods_stack(form, left_methods)
            )

            right_form = self._without_left_part(
                replace_methods_stack(form, right_methods)
            )

            left_lexeme = left_methods[-1][0].get_lexeme(left_form)
            right_lexeme = right_methods[-1][0].get_lexeme(right_form)

            return self._merge_lexemes(left_lexeme, right_lexeme)

    def _merge_lexemes(self, left_lexeme, right_lexeme):

        for left, right in self._align_lexeme_forms(left_lexeme, right_lexeme):
            word = '-'.join((left[0], right[0]))
            tag = left[1]
            normal_form = '-'.join((left[2], right[2]))
            estimate = (left[3] + right[3]) / 2
            method_stack = ((self, left[4], right[4]), )

            yield (word, tag, normal_form, estimate, method_stack)

    def _unified_grammemes(self, grammemes):
        return frozenset(self._CONSIDER_THE_SAME.get(gr, gr) for gr in grammemes)

    def _align_lexeme_forms(self, left_lexeme, right_lexeme):
        # FIXME: quadratic algorithm
        for right in right_lexeme:
            min_dist, closest = 1e6, None
            gr_right = self._unified_grammemes(right[1].grammemes)

            for left in left_lexeme:
                gr_left = self._unified_grammemes(left[1].grammemes)
                dist = len(gr_left ^ gr_right)
                if dist < min_dist:
                    min_dist = dist
                    closest = left

            yield closest, right

    @classmethod
    def _without_right_part(cls, form):
        word, tag, normal_form, estimate, methods_stack = form
        return (word[:word.index('-')], tag, normal_form[:normal_form.index('-')],
                estimate, methods_stack)

    @classmethod
    def _without_left_part(cls, form):
        word, tag, normal_form, estimate, methods_stack = form
        return (word[word.index('-')+1:], tag, normal_form[normal_form.index('-')+1:],
                estimate, methods_stack)

    @classmethod
    def _fixed_left_method_was_used(cls, left_methods):
        return not isinstance(left_methods, tuple)
