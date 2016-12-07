#!/usr/bin/python

"""
Copyright (c) 2011-2015 Nathan Boley, Marcus Stoiber

This file is part of GRIT.

GRIT is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

GRIT is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with GRIT.  If not, see <http://www.gnu.org/licenses/>.
"""

import os, sys

import multiprocessing
import queue
import time
import numpy
import re

from collections import defaultdict, namedtuple

from operator import itemgetter
from copy import copy

# import biological mods
from pysam import Fastafile

# declare constants
MIN_AAS_PER_ORF = 100

GENCODE = {
    'ATA':'I', 'ATC':'I', 'ATT':'I', 'ATG':'M',
    'ACA':'T', 'ACC':'T', 'ACG':'T', 'ACT':'T',
    'AAC':'N', 'AAT':'N', 'AAA':'K', 'AAG':'K',
    'AGC':'S', 'AGT':'S', 'AGA':'R', 'AGG':'R',
    'CTA':'L', 'CTC':'L', 'CTG':'L', 'CTT':'L',
    'CCA':'P', 'CCC':'P', 'CCG':'P', 'CCT':'P',
    'CAC':'H', 'CAT':'H', 'CAA':'Q', 'CAG':'Q',
    'CGA':'R', 'CGC':'R', 'CGG':'R', 'CGT':'R',
    'GTA':'V', 'GTC':'V', 'GTG':'V', 'GTT':'V',
    'GCA':'A', 'GCC':'A', 'GCG':'A', 'GCT':'A',
    'GAC':'D', 'GAT':'D', 'GAA':'E', 'GAG':'E',
    'GGA':'G', 'GGC':'G', 'GGG':'G', 'GGT':'G',
    'TCA':'S', 'TCC':'S', 'TCG':'S', 'TCT':'S',
    'TTC':'F', 'TTT':'F', 'TTA':'L', 'TTG':'L',
    'TAC':'Y', 'TAT':'Y', 'TAA':'_', 'TAG':'_',
    'TGC':'C', 'TGT':'C', 'TGA':'_', 'TGG':'W'}

COMP_BASES = { 'A':'T', 'T':'A', 'C':'G', 'G':'C',
               'a':'t', 't':'a', 'c':'g', 'g':'c' }

# Variables effecting .annotation.gtf output
ONLY_USE_LONGEST_ORF = False
INCLUDE_STOP_CODON = True

VERBOSE = False
MIN_VERBOSE = False

DO_PROFILE = False
SERIALIZE = False

# add parent(slide) directory to sys.path and import SLIDE mods
from ..files.gtf import Transcript
from ..files.fasta import iter_x_char_lines

################################################################################
#
# Methods to deal with genomic sequence 
#

def reverse_complement( seq ):
    """Emulate Biopython reverse_complement method, but faster
    """
    rev_comp_seq = ''
    # loop through sequence in reverse sequence
    for base in seq[::-1]:
        if base in COMP_BASES:
            # else add the compelemntary base
            rev_comp_seq += COMP_BASES[ base ]
        else:
            assert base in "nN"
            # if the base is invalid just add it to the rev_comp
            rev_comp_seq += base
    
    return rev_comp_seq

def get_gene_seq( fasta, chrm, strand, gene_start, gene_stop ):
    if not chrm.startswith( 'chr' ):
        chrm = 'chr' + chrm
    
    # get the raw sequence from the gene object and the fasta file
    # add one to stop since fasta is 0-based closed-open
    gene_seq = fasta.fetch(
        chrm, gene_start, gene_stop+1 )
    
    # convert the sequence to upper case
    gene_seq = gene_seq.upper()
    
    if strand == '-':
        gene_seq = reverse_complement( gene_seq )
    
    return gene_seq

def get_trans_seq( gene, gene_seq, trans ):
    """ get the mRNA sequence of the transcript from the gene seq
    """
    trans_seq = []
    for start, stop in trans.exons:
        # convert the coords from genomic to gene-relative
        relative_start = start - gene.start
        relative_stop = stop - gene.start
        if gene.strand == '+':
            # get the portion of the gene sequence for the current exon
            # add 1 to stop since string slice is closed-open
            trans_seq.append( gene_seq[ relative_start : relative_stop + 1 ] )
        else:
            # if the gene is neg strand reverse coords as gene_seq is rev_comp
            tmp_start = relative_start
            relative_start = len(gene_seq) - relative_stop - 1
            relative_stop = len(gene_seq) - tmp_start - 1
            # add the new sequence at the beginning since seq is rev_comp
            trans_seq.append( gene_seq[ relative_start:relative_stop+1 ] )
    
    if gene.strand == '-':
        return "".join( reversed( trans_seq ) )
    else:
        return "".join(trans_seq)

# END find genomic sequence methods
#
################################################################################

def convert_to_genomic( pos, exons ):
    rna_pos = 0
    for i, exon in enumerate(exons):
        exon_len = exon[1] - exon[0] + 1
        if rna_pos + exon_len  > pos:
            break
        rna_pos += exon_len
    
    return exons[i][0] + (pos - rna_pos)

def find_all( sequence, codon ):
    """ Returns a list of positions within sequence that are 'codon'
    """
    return [ x.start() for x in re.finditer( codon, sequence ) ]

def find_orfs( sequence ):
    """ Finds all valid open reading frames in the string 'sequence', and
    returns them as tuple of start and stop coordinates
    """    
    def grp_by_frame( locs ):
        locs_by_frame = { 0: [], 1:[], 2:[] }
        for loc in locs:
            locs_by_frame[ loc%3 ].append( loc )
        for frame in list(locs_by_frame.keys()):
            locs_by_frame[ frame ].reverse()
        return locs_by_frame

    def find_orfs_in_frame( starts, stops ):
        prev_stop = -1
        while len( starts ) > 0 and len( stops ) > 0:
            start = starts.pop()
            if start < prev_stop: continue
            stop = stops.pop()
            while stop < start and len( stops ) > 0:
                stop = stops.pop()
            if start > stop:
                assert len( stops ) == 0
                break
            if (stop - start + 1) >= (MIN_AAS_PER_ORF * 3):
                yield ( start, stop-1 )
            prev_stop = stop

        return

    # find all start and stop codon positions along sequence
    starts = find_all( sequence, 'ATG' )

    stop_amber = find_all( sequence, 'TAG' )
    stop_ochre = find_all( sequence, 'TAA' )
    stop_umber = find_all( sequence, 'TGA' )
    stops = stop_amber + stop_ochre + stop_umber
    stops.sort()
    
    orfs = []    
    starts_by_frame = grp_by_frame( starts )
    stops_by_frame = grp_by_frame( stops )
    
    for frame in (0, 1, 2):
        starts = starts_by_frame[ frame ]
        stops = stops_by_frame[ frame ]
        orfs.extend( find_orfs_in_frame(starts, stops) )
    
    return orfs

def find_cds_for_gene( gene, fasta, only_longest_orf ):
    """Find all of the unique open reading frames in a gene
    """
    annotated_transcripts = []
    gene_seq = get_gene_seq(
        fasta, gene.chrm, gene.strand, gene.start, gene.stop )
    
    for trans in gene.transcripts:
        trans_seq = get_trans_seq( gene, gene_seq, trans )
        orfs = find_orfs( trans_seq )
        if len( orfs ) == 0:
            annotated_transcripts.append( trans )
            continue
        
        filtered_orfs = []
        if only_longest_orf:
            max_orf_length = max( stop - start + 1 for start, stop in orfs )
            for start, stop in orfs:
                if stop - start + 1 == max_orf_length:
                    filtered_orfs.append( (start, stop) )
        else:
            filtered_orfs = orfs
        
        for orf_id, (start, stop) in enumerate( filtered_orfs ):
            ORF = trans_seq[start:stop+1]
            AA_seq= "".join( GENCODE[ORF[3*i:3*(i+1)]] 
                             for i in range(len(ORF)/3) )
            
            if INCLUDE_STOP_CODON:
                stop += 3
            if gene.strand == '-':
                start = len( trans_seq ) - start - 1
                stop = len( trans_seq ) - stop - 1
            
            # find the coding region boundaries in genomic coordinates
            start = convert_to_genomic( start, trans.exons )
            stop = convert_to_genomic( stop, trans.exons )
            start, stop = sorted((start, stop))
            
            new_trans = copy(trans)
            if len( filtered_orfs ) > 1:
                new_trans.id = new_trans.id + "_CDS%i" % (orf_id+1)
            new_trans.add_cds_region((start, stop), AA_seq)
            annotated_transcripts.append( new_trans )
    
    return annotated_transcripts

def find_gene_orfs_worker( input_queue, gtf_ofp, fa_ofp, fasta_fn ):
    # open fasta file in each thread separately
    fasta = Fastafile( fasta_fn )
    
    # process genes for orfs until input queue is empty
    while not input_queue.empty():
        try:
            gene = input_queue.get(block=False)
        except queue.Empty:
            break
        
        if VERBOSE: print('\tProcessing ' + gene.id, file=sys.stderr)
        ann_trans = find_cds_for_gene( gene, fasta, ONLY_USE_LONGEST_ORF )
        op_str = "\n".join( [ tr.build_gtf_lines( gene.id, {} ) 
                              for tr in ann_trans ] )
        gtf_ofp.write( op_str + "\n" )
        
        if fa_ofp != None:
            for trans in ann_trans:
                fa_ofp.write( ">%s\n" % trans.id )
                for line in iter_x_char_lines(trans.coding_sequence):
                    fa_ofp.write(line+"\n")
                
        if VERBOSE: print('\tFinished ' + gene.id, file=sys.stderr)
    
    return


def find_all_orfs( genes, fasta_fn, gtf_ofp, fa_ofp, num_threads=1 ):
    # create queues to store input and output data
    manager = multiprocessing.Manager()
    input_queue = manager.Queue()
    
    if MIN_VERBOSE: print('Processing all transcripts for ORFs.', file=sys.stderr)
    
    # populate input_queue
    for gene in genes:
        input_queue.put( gene )

    # spawn threads to find the orfs, and write them to the output streams 
    args = ( input_queue, gtf_ofp, fa_ofp, fasta_fn )
    if num_threads == 1:
        find_gene_orfs_worker(*args)
    else:
        processes = []
        for thread_id in range( num_threads ):
            p = multiprocessing.Process(target=find_gene_orfs_worker, args=args)
            p.start()
            processes.append( p )

        for p in processes:
            p.join()
    
    return
