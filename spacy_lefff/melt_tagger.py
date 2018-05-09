#!/usr/bin/env python
# -*- coding: utf-8 -*-
#################################################################################
## Copyright (C) 2009 Pascal Denis and Benoit Sagot
##
## This library is free software; you can redistribute it and#or
## modify it under the terms of the GNU Lesser General Public
## License as published by the Free Software Foundation; either
## version 3.0 of the License, or (at your option) any later version.
##
## This library is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
## Lesser General Public License for more details.
##
## You should have received a copy of the GNU Lesser General Public
## License along with this library; if not, write to the Free Software
## Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#################################################################################

import os
import sys
import re
import math
import tempfile
import codecs
import operator
import time
import optparse
import unicodedata
import subprocess
from collections import defaultdict
import logging

WD_TAG_RE = re.compile(r'^(.+)/([^\/]+)$')
CAPONLYLINE_RE = re.compile(r'^([^a-z]+)$')
number = re.compile("\d")
hyphen = re.compile("\-")
equals = re.compile("=")
upper = re.compile("^([A-Z]|[^_].*[A-Z])")
allcaps = re.compile("^[A-Z]+$")

try:
    import numpy as np
except ImportError:
    sys.exit('This module requires that numpy be installed')

# Import Psyco if available
try:
    import psyco
    psyco.full()
except ImportError:
    pass

from json import dumps, loads
import io
from spacy.tokens import Token as tk
from lefff import LefffLemmatizer

LOGGER = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models/fr')

LEXICON_FILE = os.path.join(MODELS_DIR, 'lexicon.json')
TAG_DICT = os.path.join(MODELS_DIR, 'tag_dict.json')

# extra options dict for feature selection
feat_select_options = {
    # previous default values
    # 'win':2, # context window size
    # 'pwin':2, # context window size for predicted tags (left context)
    # 'lex_wd':1, # lefff current word features
    # 'lex_lhs':1, # lefff LHS context features
    # 'lex_rhs':1, # lefff RHS context features
    # 'pln':4,
    # 'sln':4,
    # 'rpln':1,
    # 'rsln':0,
    # 'ffthrsld':2, # min feat occ: will discard any features occurring (strictly) less than ffthrsld times in the training data
    # 'norm':0, # normalization (0=none, 1=L1, 2=L2)
    # new default values (Sagot HDR)
    'win':2, # context window size
    'pwin':2, # context window size for predicted tags (left context)
    'lex_wd':1, # lefff current word features
    'lex_lhs':1, # lefff LHS context features
    'lex_rhs':1, # lefff RHS context features
    'pln':4,
    'sln':5,
    'rpln':3,
    'rsln':3,
    'ffthrsld':1, # min feat occ: will discard any features occurring (strictly) less than ffthrsld times in the training data
    'norm':0, # normalization (0=none, 1=L1, 2=L2)
    }

############################ pos_tagger.py ############################

class POSTagger:

    def __init__(self, data_dir=DATA_DIR, lexicon_file_name=LEXICON_FILE, tag_file_name=TAG_DICT, print_probas=False):
        tk.set_extension('melt_tagger', default=None)
        LOGGER.info("  TAGGER: Loading lexicon...")
        self.lex_dict = unserialize(lexicon_file_name)
        LOGGER.info("  TAGGER: Loading tags...")
        self.tag_dict = unserialize(tag_file_name)
        self.classifier = MaxEntClassifier()
        self.cache = {}
        self.load_model()
        #print the probability of the tag along to the tag itself
        self.print_probas= print_probas
        return


    def load_model(self,model_path=MODELS_DIR):
        try:
            self.classifier.load( model_path )
        except Exception,e:
            sys.exit("Error: Failure load POS model from %s (%s)" %(model_path,e))
        return


    def train_model(self,_file, model_path, prior_prec=1, maxit=100, repeat=5, classifier="multitron", feat_options=feat_select_options, dump_raw_model=False,dump_training_data=False,zh_mode = False,norm=0):
        LOGGER.info("  TAGGER (TRAIN): Generating training data...          ")
        reader = "Brown"
        if zh_mode :
            reader = "Weighted"
        train_inst_file = self.generate_training_data( _file,
                                                       feat_options=feat_options,
                                                       dirpath=model_path,
                                                       dump_training_data=dump_training_data,
                                                       Reader=reader)
        LOGGER.info("  TAGGER (TRAIN): Generating training data: done          ")
        LOGGER.info("  TAGGER (TRAIN): Training POS model...")
        self.classifier.train_megam( train_inst_file,
                                     repeat=repeat,
                                     classifier=classifier,
                                     maxit=maxit,
                                     prior_prec=prior_prec,
                                     dump_raw_model=dump_raw_model,
                                     dirpath=model_path,
                                     norm=norm)
        self.classifier.dump( model_path )
        LOGGER.info( "  TAGGER (TRAIN): Clean-up data file...")
        os.unlink(train_inst_file)
        print  >> sys.stderr, "  TAGGER (TRAIN): Clean-up data file: done"
        return


    def generate_training_data(self, infile, feat_options=feat_select_options, encoding='utf-8',dirpath="MElt_tmp_dir",dump_training_data=False,Reader="Brown"):
        data_file_name = tempfile.mktemp()
        data_file = codecs.open(data_file_name,'w',encoding)
        if dump_training_data:
            data_dump_file = codecs.open(dirpath+".trainingdata",'w',encoding)
        inst_ct = 0
        if Reader == "Brown":
            CorpusR = BrownReader
        if Reader == "Weighted":
            CorpusR = WeightedReader
        for s in CorpusR(infile):
            # build token list for each sentence (urgh! FIXME)
            tokens = []
            if Reader == "Weighted" :
                weight,s = s
            for wd,tag in s:
                token = Token( string=wd, pos=tag )
                token.label = token.pos # label is POS tag
                tokens.append( token )
            # create training instance for each token
            for i in range(len(tokens)):
                if tokens[i].label == "__SPECIAL__":
                    print >> data_file, tokens[i].string
                    if dump_training_data:
                        print >> data_dump_file, tokens[i].string
                else:
                    inst_ct += 1
                    os.write(1, "%s" %"\b"*len(str(inst_ct))+str(inst_ct))
                    inst = Instance( label=tokens[i].label,\
                                    index=i, tokens=tokens,\
                                    feat_selection=feat_options,\
                                    lex_dict=self.lex_dict,\
                                    tag_dict=self.tag_dict,\
                                    cache=self.cache )
                    inst.get_features()
                    if Reader == "Weighted":
                        print >> data_file, inst.weighted_str(weight)
                        if dump_training_data:
                            print >> data_dump_file, inst.weighted_str(weight)
                    else :
                        print >> data_file, inst.__str__()
                        if dump_training_data:
                            print >> data_dump_file, inst.__str__()
                        # print >> sys.stderr, inst.__str__().encode('utf8')
        data_file.close()
        if dump_training_data:
             data_dump_file.close()
        os.write(1, "%s" %"\b"*len(str(inst_ct))+"filtering")
        features_filter_exec_path = where_is_exec("MElt_features_filtering.pl")
        os.system("/usr/local/bin/MElt_features_filtering.pl \"%s\" %d" %(data_file_name,feat_options.get('ffthrsld',2)));
        os.write(1,'\n')
        return data_file_name



    def tag_token_sequence(self, tokens, feat_options=feat_select_options, beam_size=3):
        ''' N-best breath search for the best tag sequence for each sentence'''
        # maintain N-best sequences of tagged tokens
        sequences = [([],0.0)]  # log prob.
        for i,token in enumerate(tokens):
            n_best_sequences = []
            # cache static features
            cached_inst = Instance( label=tokens[i].label,
                                    index=i, tokens=tokens,
                                    feat_selection=feat_options,
                                    lex_dict=self.lex_dict,
                                    tag_dict=self.tag_dict,
                                    cache=self.cache )
            cached_inst.get_static_features()
            # get possible tags: union of tags found in tag_dict and
            # lex_dict
            wd = token.string
            wasCap = token.wasCap
            legit_tags1 = self.tag_dict.get(wd,{})
            legit_tags2 = self.lex_dict.get(wd,{})
#            legit_tags2 = {} # self.lex_dict.get(wd,{})
#            print >> sys.stderr, "legit_tags1: ", [t for t in legit_tags1]
            for j,seq in enumerate(sequences):
                seq_j,log_pr_j = sequences[j]
                tokens_j = seq_j+tokens[i:] # tokens with previous labels
                # classify token
                inst = Instance( label=tokens[i].label,
                                 index=i, tokens=tokens_j,
                                 feat_selection=feat_options,
                                 lex_dict=self.lex_dict,
                                 tag_dict=self.tag_dict,
                                 cache=self.cache )
                inst.fv = cached_inst.fv[:]
                inst.get_sequential_features()
                label_pr_distrib = self.classifier.class_distribution(inst.fv)
                # extend sequence j with current token
                for (cl,pr) in label_pr_distrib:
                    # make sure that cl is a legal tag
                    if legit_tags1 or legit_tags2:
                        if (cl not in legit_tags1) and (cl not in legit_tags2):
                            continue
                    labelled_token = Token(string=token.string,pos=token.pos,\
                                           comment=token.comment,\
                                           wasCap=wasCap,\
                                           label=cl,proba=pr,label_pr_distrib=label_pr_distrib)
                    n_best_sequences.append((seq_j+[labelled_token],log_pr_j+math.log(pr)))
            # sort sequences
            n_best_sequences.sort( key=operator.itemgetter(1) )
            #debug_n_best_sequence(n_best_sequences)
            # keep N best
            sequences = n_best_sequences[-beam_size:]
        # return sequence with highest prob.
        best_sequence = sequences[-1][0]
        # print >> sys.stderr, "Best tok seq:", [(t.string,t.label) for t in best_sequence]
        return best_sequence


    def __call__(self, doc, handle_comments=False, feat_options=feat_select_options, beam_size=3, lowerCaseCapOnly=False,zh_mode=False):
        LOGGER.info("  TAGGER: POS Tagging...")
        t0 = time.time()
        # process sentences
        s_ct = 0
        if (handle_comments):
            comment_re = re.compile(r'^{.*} ')
            split_re = re.compile(r'(?<!\}) ')
            token_re = re.compile(r'(?:{[^}]*} *)?[^ ]+')
        else:
            split_re = re.compile(r' ')
            token_re = re.compile(r'[^ ]+')
        line = " ".join([w.text for w in doc])
        wasCapOnly = 0
        if (lowerCaseCapOnly and len(line) > 10):
            wasCapOnly = CAPONLYLINE_RE.match(line)
        if (wasCapOnly):
            wasCapOnly = 1
        else:
            wasCapOnly = 0
        if (wasCapOnly):
            line = line.lower()
#                LOGGER.info( "CAPONLY: "+line
        wds = []
#            wds = split_re.split(line)
        result = token_re.match(line)
        while (result):
            wds.append( result.group() )
            line = token_re.sub("",line,1)
            line = line.strip(' \n')
            result = token_re.match(line)
        tokens = []
        for wd in wds:
            token = Token( string=wd, wasCap=wasCapOnly )
            tokens.append( token )
        tagged_tokens = self.tag_token_sequence( tokens,
                                                 feat_options=feat_options,
                                                 beam_size=beam_size )
        if (self.print_probas):
            tagged_sent = " ".join( [tok.__pstr__() for tok in tagged_tokens] )
        else:
            tagged_sent = " ".join( [tok.__str__() for tok in tagged_tokens] )
        for w, t in zip(doc, tagged_tokens):
            w._.melt_tagger = t.label
        return doc


    def load_tag_dictionary(self, filepath ):
        LOGGER.info("  TAGGER: Loading tag dictionary...")
        self.tag_dict = unserialize( filepath )
        LOGGER.info("  TAGGER: Loading tag dictionary: done")
        return


    def load_lexicon(self, filepath ):
        LOGGER.info("  TAGGER: Loading external lexicon...")
        self.lex_dict = unserialize( filepath )
        LOGGER.info("  TAGGER: Loading external lexicon: done")
        return


############################ corpus_reader.py ############################


class CorpusReader:
    pass


class BrownReader(CorpusReader):

    """
    Data reader for corpus in the Brown format:

    Le/DET prix/NC de/P vente/NC du/P+D journal/NC n'/ADV a/V pas/ADV <E9>t<E9>/VPP divulgu<E9>/VPP ./PONCT
    Le/DET cabinet/NC Arthur/NPP Andersen/NPP ,/PONCT administrateur/NC judiciaire/ADJ du/P+D titre/NC depuis/P l'/DET effondrement/NC de/P l'/DET empire/NC Maxwell/NPP en/P d<E9>cembre/NC 1991/NC ,/PO
    NCT a/V indiqu<E9>/VPP que/CS les/DET nouveaux/ADJ propri<E9>taires/NC allaient/V poursuivre/VINF la/DET publication/NC ./PONCT

    """

    def __init__(self,infile, encoding='utf-8'):
        self.stream = codecs.open(infile, 'r', encoding)
        return

    def __iter__(self):
        return self

    def next(self,lowerCaseCapOnly=0):
        line = self.stream.readline()
        if (line == ''):
            self.stream.seek(0)
            raise StopIteration
        line = line.strip(' \n')
        token_list = []
        if line == 'DEV':
            token_list.append((line,"__SPECIAL__"))
        else:
            wasCapOnly = 0
            if (lowerCaseCapOnly == 1 and len(line) > 10):
                wasCapOnly = CAPONLYLINE_RE.match(line)
            for item in line.split(' '):
                if item == '':
                    continue
                wdtag = WD_TAG_RE.match(item)
                if (wdtag):
                    wd,tag = wdtag.groups()
                    if (wasCapOnly):
                        token_list.append( (wd.lower(),tag) )
                    else:
                        token_list.append( (wd,tag) )
                else:
                    LOGGER.info("Warning: Incorrect token/tag pair: \""+(item.encode('utf8'))+"\""+" --- line: "+line)
        return token_list

####################Weigthed Corpus########################

class WeightedReader(CorpusReader):
    def __init__(self,infile,encoding='utf8'):
        self.stream = codecs.open(infile, 'r', encoding)
        return
    def __iter__(self):
        return self
    def next(self):
        line = self.stream.readline().strip()
        if (line == ''):
            self.stream.seek(0)
            raise StopIteration
        weight,tokens = line.split("\t",1)
        w = 1. / float(weight)
        tokens = [ tuple(tok.rsplit("_",1)) for tok in tokens.split() ]
        return (w,tokens)

############################ my_token.py ############################


class Token:

    def __init__(self, string=None, wasCap=0, pos=None, label=None, proba=None, comment=None, label_pr_distrib=[],index=None,position=None):
        if type(string) is tuple and isinstance(string[2],sxp.Token) : #DAG
            self.string = string[2].forme
            self.position = tuple(string[0:2])
            self.tokobj = string[2]
        elif isinstance(string,Token) :
            self.string = string.string
            self.position = string.position
            self.tokobj = string.tokobj
        else :
            self.position = position
            self.string = string
            self.comment = comment
        self.index = index
        self.wasCap = wasCap
        self.pos = pos
        self.label = label
        self.proba = proba
        self.comment = comment
        self.label_pr_distrib = label_pr_distrib
        if (self.comment == None):
            self.comment = ""
        return

    def set_label(self, label):
        self.label = label
        return

    def __str__(self):
        if hasattr(self, 'tokobj'):
            r = ""
            if self.tokobj.commentaire != "":
                r += "{%s} " %(self.tokobj.commentaire,)
            r += "%s__%s" %(self.string,self.label)
            if self.tokobj.semantique != "":
                r += "[|%s|] " %(self.tokobj.semantique,)
            return r
        if (self.wasCap):
            return "%s%s/%s" %(self.comment,self.string.upper(),self.label)
        else:
            return "%s%s/%s" %(self.comment,self.string,self.label)

    def __pstr__(self):
        if (self.wasCap):
            return "%s%s/%s/%s" %(self.comment,self.string.upper(),self.label,self.proba)
        else:
            return "%s%s/%s/%s" %(self.comment,self.string,self.label,self.proba)





############################ classifier.py ############################


class MaxEntClassifier:

    def __init__( self ):
        self.classes = []
        self.feature2int = {}
        self.weights = np.zeros( (0,0) )
        self.bias_weights = np.zeros( (0,0) )
        return


    def load(self, dirpath ):
        LOGGER.info("  TAGGER: Loading model from %s..." %dirpath)
        self.classes = unserialize( os.path.join(dirpath, 'classes.json'))
        self.feature2int = unserialize( os.path.join(dirpath, 'feature_map.json'))
        self.weights = np.load( os.path.join(dirpath, 'weights.npy'))
        self.bias_weights = np.load( os.path.join(dirpath, 'bias_weights.npy'))
        LOGGER.info("  TAGGER: Loading model from %s: done" %dirpath)
        return


    def dump(self, dirpath):
        LOGGER.info("  TAGGER (TRAIN): Dumping model in %s..." %dirpath)
        serialize(self.classes, os.path.join(dirpath, 'classes.json'))
        serialize(self.feature2int, os.path.join(dirpath, 'feature_map.json'))
        self.weights.dump( os.path.join(dirpath, 'weights.npy'))
        self.bias_weights.dump( os.path.join(dirpath, 'bias_weights.npy'))
        LOGGER.info("  TAGGER (TRAIN): Dumping model in %s: done." %dirpath)
        return



    def train_megam( self, datafile, prior_prec=1, repeat=5, classifier="multitron", maxit=100, bias=True, dump_raw_model=False, dirpath="MElt_tmp_dir", norm=0 ):

        if os.environ.get("MEGAM_DIR",None) == "":
            megam_exec_path = where_is_exec("megam.opt")
            if megam_exec_path == "":
                megam_exec_path = where_is_exec("megam.exe")
                if megam_exec_path == "":
                    sys.exit("Missing env variable for MEGAM_DIR. You need Megam to train models. The MEGAM_DIR variable must be set in your environment and store the path to the directory that contains the megam.opt executable")
        else:
            if os.access(os.environ.get("MEGAM_DIR",None), os.F_OK):
                if os.access(os.environ.get("MEGAM_DIR",None)+"/megam.opt", os.F_OK):
                    megam_exec_path = os.environ.get("MEGAM_DIR",None)+"/megam.opt"
                elif os.access(os.environ.get("MEGAM_DIR",None)+"/megam.exe", os.F_OK):
                    megam_exec_path = os.environ.get("MEGAM_DIR",None)+"/megam.exe"
                else:
                    sys.exit("Could not find megam.opt or megam.exe within the folder specified in the MEGAM_DIR environment variable. You need Megam to train models. The MEGAM_DIR variable must be set in your environment and store the path to the directory that contains the megam.opt or megam.exe executable")
            else:
                sys.exit("The folder specified in the MEGAM_DIR environment variable could not be found. You need Megam to train models. The MEGAM_DIR variable must be set in your environment and store the path to the directory that contains the megam.opt or megam.exe executable")

        if not os.access(os.environ.get("MEGAM_DIR",None)+"/megam.opt", os.X_OK):
            sys.exit("The version of megam (.opt or .exe) that was found is not executable, or you don't have the permissions for executing it. You need Megam to train models. The MEGAM_DIR variable must be set in your environment and store the path to the directory that contains the megam.opt or megam.exe executable")


        """ simple call to Megam executable for multiclass
        classification with some relevant options:

        -prior_prec: precision of Gaussian prior (megam default:1). It's
         the inverse variance. See http://www.cs.utah.edu/~hal/docs/daume04cg-bfgs.pdf.
        -repeat: repeat optimization <int> times (megam default:1)
        -maxit: max # of iterations (megam default:100)
        """

        LOGGER.info("  TAGGER (TRAIN): Training Megam classifier (using "+megam_exec_path+")...")
        # build process command
        proc = [megam_exec_path, "-nc", "-repeat", repeat, "-lambda", prior_prec, "-maxi", maxit]
        if not bias:
            proc.append("-nobias")
        if norm != 0:
            proc.append("-norm"+str(norm))
        proc.append(classifier) # optimization type: multitron / multiclass
        proc.append(datafile)
        print >> sys.stderr, proc
        proc = map(str,proc)
        # run process
        p = subprocess.Popen(proc, stdout=subprocess.PIPE)
        (outdata, errdata) = p.communicate()
        # check return code
        if p.returncode != 0:
            print >> sys.stderr, errdata
            raise OSError("Error while trying to execute "+" ".join(proc))
        if dump_raw_model:
            raw_model_file = open(dirpath+".rawmodel", 'w')
            raw_model_file.write(outdata)
            raw_model_file.close
        # load model from megam output
        self.process_megam( outdata )
        # print basic model info
        print >> sys.stderr, "# of classes: %s" %len(self.classes)
        print >> sys.stderr, "# of features: %s" %len(self.feature2int)
        return




    def process_megam( self, megam_str, encoding="utf-8" ):
        ''' process megam parameter file --- only supports multiclass
        named classes at the moment'''
        nc_str = "***NAMEDLABELSIDS***"
        bias_str = "**BIAS**"
        lines = megam_str.strip().split('\n')
        # set classes
        line = lines[0]
        if line.startswith(nc_str):
            self.classes = map( str, line.split()[1:] )
            lines.pop(0)
        else:
            raise OSError("Error while reading Megam output: %s not class line" %line)
        # bias weights
        line = lines[0]
        if line.startswith(bias_str):
            items = line.split()
            self.bias_weights = np.array( map(float, items[1:]) )
            lines.pop(0)
        else:
            self.bias_weights = np.zeros( (len(lines),len(self.classes)) )
        # set feature map and weight matrix
        self.weights = np.zeros( (len(lines),len(self.classes)) )
        for i,line in enumerate(lines):
            items = line.strip().split()
            fname = items[0]
            self.feature2int[fname] = i
            self.weights[i] = np.array( map(float, items[1:]) )
        return



    def categorize( self, features ):
        """ sum over feature weights and return class that receives
        highest overall weight
        """
        weights = self.bias_weights
        for f in features:
            fint = self.feature2int.get(f,None)
            if not fint:
                continue
            fweights = self.weights[fint]
            # summing bias and fweights
            weights = weights+fweights
        # find highest weight sum
        best_weight = weights.max()
        # return class corresponding to highest weight sum
        best_cl_index = np.nonzero(weights == best_weight)[0][0]
        return self.classes[best_cl_index]



    def class_distribution( self, features ):
        """ probability distribution over the different classes
        """
        # print >> sys.stderr, "event: %s" % features
        weights = self.bias_weights
        for f in features:
            fint = self.feature2int.get(f,None)
            if fint is None:
                continue
            fweights = self.weights[fint]
            # summing bias and fweights
            weights = weights+fweights
        # exponentiation of weight sum
        scores = map( math.exp, list(weights) )
        # compute normalization constant Z
        z = sum( scores )
        # compute probabilities
        probs = [ s/z for s in scores ]
        # return class/prob map
        return zip( self.classes, probs )



############################ instance.py ############################


class Instance:

    def __init__(self, index, tokens, label=None, lex_dict={}, tag_dict={},
                 feat_selection={}, cache={}):
        self.label = label
        self.fv = []
        self.feat_selection = feat_selection
        # token
        self.token = tokens[index]
        self.index = index
        self.word = self.token.string
        # lexicons
        self.lex_dict = lex_dict
        self.tag_dict = tag_dict
        self.cache = cache ## TODO
        # contexts
        win = feat_selection.get('win',2)
        pwin = feat_selection.get('pwin',2)
        self.context_window = win
        self.ptag_context_window = pwin
        self.set_contexts( tokens, index, win, pwin )
        return


    def set_contexts(self, toks, idx, win, pwin):
        rwin = win
        lwin = max (win, pwin)
        lconx = toks[:idx][-lwin:]
        rconx = toks[idx+1:][:rwin]
        self.left_wds = [tok.string for tok in lconx]
        if len(self.left_wds) < lwin:
            self.left_wds = ["<s>"] + self.left_wds
        self.left_labels = [tok.label for tok in lconx]
        self.right_wds = [tok.string for tok in rconx]
        if len(self.right_wds) < rwin:
            self.right_wds += ["</s>"]
        self.lex_left_tags = {}
        self.lex_right_tags = {}
        if self.lex_dict:
            self.lex_left_tags = ["|".join(self.lex_dict.get(tok.string,{"unk":1}).keys())
                                  for tok in lconx if tok is not None]
            self.lex_right_tags = ["|".join(self.lex_dict.get(tok.string,{"unk":1}).keys())
                                   for tok in rconx if tok is not None]
        if self.tag_dict:
            self.train_left_tags = ["|".join(self.tag_dict.get(tok.string,{"unk":1}).keys())
                                    for tok in lconx if tok is not None]
            self.train_right_tags = ["|".join(self.tag_dict.get(tok.string,{"unk":1}).keys())
                                    for tok in rconx if tok is not None]
        return


    def add(self,name,key,value=-1):
        if value == -1:
            f = u'%s=%s' %(name,key)
        else:
            f = u'%s=%s=%s' %(name,key,value)
        self.fv.append( f )
        return f


    def add_cached_feats(self,features):
        self.fv.extend(features)
        return


    def __str__(self):
        return u'%s\t%s' %(self.label,u" ".join(self.fv))
    def weighted_str(self,w):
        return u'%s $$$WEIGHT %f\t%s' %(self.label,w,u" ".join(self.fv))

    def get_features(self):
        self.get_static_features()
        self.get_sequential_features()
        return


    def get_sequential_features(self):
        ''' features based on preceding tagging decisions '''
        prev_labels = self.left_labels
        for n in range(1,self.ptag_context_window+1):
            if len(prev_labels) >= n:
                # unigram for each position
                if n == 1:
                    unigram = prev_labels[-n]
                else:
                    unigram = prev_labels[-n:-n+1][0]
                self.add('ptag-%s' %n, unigram)
                if n > 1:
                    # ngrams where 1 < n < window
                    ngram = prev_labels[:n]
                    self.add('ptagS-%s' %n, "#".join(ngram))
        # surronding contexts (left context = predicted tag, right context = lexical info)
        lex_rhs_feats = self.feat_selection.get('lex_rhs',0)
        rtags = self.lex_right_tags
        if lex_rhs_feats:
            if (len(prev_labels) >= 1) and (len(rtags) >= 1):
                self.add('lpred-rlex-surr', prev_labels[-1] + "#" + rtags[0])
        return


    def get_static_features(self):
        ''' features that can be computed independently from previous
        decisions'''
        self.get_word_features()
        self.get_conx_features()
        if self.lex_dict:
            self.add_lexicon_features()
        # NOTE: features for tag dict currently turned off
        # if self.tag_dict:
        #     self.add_tag_dict_features()
        return


    def get_word_features(self):
        ''' features computed based on word form: word form itself,
        prefix/suffix-es of length ln: 0 < n < ln, and certain regex
        patterns'''
        pln = self.feat_selection.get('pln',4) # 5
        sln = self.feat_selection.get('sln',4) # 5
        word = self.word
        index = self.index
        dico = self.lex_dict
        lex_tags = dico.get(word,{})
        # selecting the suffix confidence class for the word
        val = 1;
        if len(lex_tags) == 1:
            val = lex_tags.values()[0]
        else:
            val = 1
            for v in lex_tags.values():
                if v == "0":
                    val = 0
                    break
        # word string-based features
        if word in self.cache:
            # if wd has been seen, use cache
            self.add_cached_features(self.cache[word])
        else:
            # word string
            self.add('wd',word)
            # suffix/prefix
            wd_ln = len(word)
            if pln > 0:
                for i in range(1,pln+1):
                    if wd_ln >= i:
                        self.add('pref%i' %i, word[:i])
            if sln > 0:
                for i in range(1,sln+1):
                    if wd_ln >= i:
                        self.add('suff%i' %i, word[-i:], val)
        # regex-based features
        self.add( 'nb', number.search(word) != None )
        self.add( 'hyph', hyphen.search(word) != None )
#        self.add( 'eq', equals.search(word) != None )
        uc = upper.search(word)
        self.add( 'uc', uc != None)
        self.add( 'niuc', uc != None and index > 0)
        self.add( 'auc', allcaps.match(word) != None)
        return



    def get_conx_features(self):
        ''' ngrams word forms in left and right contexts '''
        rpln = self.feat_selection.get('rpln',1)
        rsln = self.feat_selection.get('rsln',1)
        win = self.context_window
        lwds = self.left_wds
        rwds = self.right_wds
        # left/right contexts: ONLY UNIGRAMS FOR NOW
        for n in range(1,win+1):
            # LHS
            if len(lwds) >= n:
                # unigram
                if n == 1:
                    left_unigram = lwds[-n]
                else:
                    left_unigram = lwds[-n:-n+1][0]
                self.add('wd-%s' %n, left_unigram)
                # ngram
                # if n > 1:
                #    left_ngram = lwds[-n:]
                #    self.add('wdS-%s' %n, "#".join(left_ngram))
            # RHS
            if len(rwds) >= n:
                # unigram
                right_unigram = rwds[n-1:n][0]
                self.add('wd+%s' %n, right_unigram)
                if n == 1:
                    # adding light suffix information for the right context
                    wd_ln = len(right_unigram)
                    #                    print >> sys.stderr, "right_unigram = %s (wd_ln = %i)" %(right_unigram,wd_ln)
                    #                    print >> sys.stderr, "  rsln = %i" %(rsln)
                    if rpln > 0:
                        for i in range(1,rpln+1):
                            if wd_ln >= i:
                                self.add('pref+1-%i' %i, right_unigram[:i])
                    if rsln > 0:
                        for i in range(1,rsln+1):
                            if wd_ln >= i:
                                self.add('suff+1-%i' %i, right_unigram[-i:])
                # ngram
                # if n > 1:
                #    right_ngram = rwds[:n]
                #    self.add('wdS+%s' %n, "#".join(right_ngram))
        # surronding contexts
        if win % 2 == 0:
            win /= 2
            for n in range(1,win+1):
                surr_ngram = lwds[-n:] + rwds[:n]
                if len(surr_ngram) == 2*n:
                    self.add('surr_wds-%s' %n, "#".join(surr_ngram))

        return



    def _add_lex_features(self, dico, ltags, rtags, feat_suffix): # for lex name
        lex_wd_feats = self.feat_selection.get('lex_wd',0)
        lex_lhs_feats = self.feat_selection.get('lex_lhs',0)
        lex_rhs_feats = self.feat_selection.get('lex_rhs',0)
        if lex_wd_feats:
            # ------------------------------------------------------------
            # current word
            # ------------------------------------------------------------
            word = self.word
            uc = upper.search(word)
            lex_tags = dico.get(word,{})
            if not lex_tags and self.index == 0:
                # try lc'ed version for sent initial words
                lex_tags = dico.get(word.lower(),{})
            if len(lex_tags) == 0:
                self.add('%s' %feat_suffix, "unk")
            elif len(lex_tags) == 1:
                # unique tag
                t = lex_tags.keys()[0]
                self.add('%s-u' %feat_suffix,t,lex_tags[t])
            else:
                # disjunctive tag
                self.add('%s-disj' %feat_suffix,"|".join(lex_tags))
                # individual tags in disjunction
                for t in lex_tags:
                    self.add('%s-in' %feat_suffix,t)
                    # ?                   f = u'%s=%s:%s' %(feat_suffix,t,lex_tags[t])
            if uc != None:
                uc_lex_tags = dico.get(word.lower(),{})
                if len(uc_lex_tags) == 0:
                    self.add('%s' %feat_suffix, "uc-unk")
                elif len(uc_lex_tags) == 1:
                    # unique tag
                    t = uc_lex_tags.keys()[0]
                    self.add('%s-uc-u' %feat_suffix,t,uc_lex_tags[t])
                else:
                    # disjunctive tag
                    self.add('%s-uc-disj' %feat_suffix,"|".join(uc_lex_tags))
                    # individual tags in disjunction
                    for t in uc_lex_tags:
                        self.add('%s-uc-in' %feat_suffix,t)
        # left and right contexts
        win = self.context_window
        for n in range(1,win+1):
            # ------------------------------------------------------------
            # LHS -> lower results with those (understandable: predicted tag is a similar but less ambiguous source of info)
            # ------------------------------------------------------------
            # if lex_lhs_feats:
            #     if len(ltags) >= n:
            #         # unigram
            #         if n == 1:
            #             left_unigram = ltags[-n]
            #         else:
            #             left_unigram = ltags[-n:-n+1][0]
            #         self.add('%s-%s' %(feat_suffix,n), left_unigram)
            #         # ngram
            #         if n > 1:
            #             left_ngram = ltags[-n:]
            #             self.add('%sS-%s' %(feat_suffix,n), "#".join(left_ngram))
            # ------------------------------------------------------------
            # RHS
            # ------------------------------------------------------------
            if lex_rhs_feats:
                if len(rtags) >= n:
                    # unigram
                    right_unigram = rtags[n-1:n][0]
                    self.add('%s+%s' %(feat_suffix,n), right_unigram)
                    # ngram
                    if n > 1:
                        right_ngram = rtags[:n]
                        self.add('%sS+%s' %(feat_suffix,n), "#".join(right_ngram))

        # surronding purely lexical contexts (left context = lexical info, not predicted tag)
        # if lex_lhs_feats and lex_rhs_feats:
        #     if win % 2 == 0:
        #         win /= 2
        #         for n in range(1,win+1):
        #             surr_ngram = ltags[-n:] + rtags[:n]
        #             if len(surr_ngram) == 2*n:
        #                 self.add('%s-surr-%s' %(feat_suffix,n), "#".join(surr_ngram))

        return


    def add_lexicon_features(self):
        lex = self.lex_dict
        l_tags = self.lex_left_tags
        r_tags = self.lex_right_tags
        self._add_lex_features( lex, l_tags, r_tags, feat_suffix='lex')
        return


    def add_tag_dict_features(self):
        lex = self.tag_dict
        l_tags = self.train_left_tags
        r_tags = self.train_right_tags
        self._add_lex_features( lex, l_tags, r_tags, feat_suffix='tdict' )
        return
############################ utils.py ############################

def debug_n_best_sequence(n_best_sequences):
    print "debug"
    print ("\n".join([ "%s/%.2f" % (" ".join([unicode(t) for t in l]),s)  for l,s in n_best_sequences])).encode("utf8")
    print "----"

def tag_dict(file_path):
    tag_dict = defaultdict(dict)
    for s in BrownReader(file_path):
        for wd,tag in s:
            if tag != "__SPECIAL__":
                tag_dict[wd][tag] = 1
    return tag_dict



def word_list(file_path,t=5):
    word_ct = {}
    for s in BrownReader(file_path):
        for wd,tag in s:
            if tag != "__SPECIAL__":
                word_ct[wd] =  word_ct.get(wd,0) + 1
    filtered_wd_list = {}
    for w in word_ct:
        ct = word_ct[w]
        if ct >= t:
            filtered_wd_list[w] = ct
    return filtered_wd_list



def unserialize(filepath, encoding="utf-8"):
    _file = codecs.open( filepath, 'r', encoding=encoding )
    datastruct = loads( _file.read() )
    _file.close()
    return datastruct



def serialize(datastruct, filepath, encoding="utf-8"):
    _file = codecs.open( filepath, 'w', encoding=encoding )
    _file.write( dumps( datastruct ) )
    _file.close()
    return

def filter_tokens(tokens,i):
    u'''retourne la liste de token privée de ceux avant le i-eme et avec le i-eme en premier'''
    tok = tokens[i]
    result = filter(lambda x: x.position[0] > tok.position[0],tokens)
    return [tok] + result

def suivants_in_dag(tokens,i):
    if i is None :
        fin = 0
        i = -1
    else :
        fin = tokens[i].position[1]
    suivants = []
    for tok in tokens[i+1:len(tokens)]:
        if tok.position[0] == fin :
           suivants.append(tok)
    return suivants

def where_is_exec(program):
    u''' retourne le chemin d'un executable si il est trouvé dans le PATH'''
    import os
    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, fname = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file

    return NonePOSTagger