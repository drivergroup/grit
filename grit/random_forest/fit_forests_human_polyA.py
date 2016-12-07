"""
Copyright (c) 2011-2015 James Bentley Brown, Nathan Boley

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
import sys, os 

import copy
import numpy
import time
import pickle

from collections import namedtuple


from sklearn.ensemble import RandomForestClassifier
from bx.intervals.intersection import Intersecter, Interval

from ..files.gtf import load_gtf, iter_gff_lines
from ..files.reads import RNAseqReads, clean_chr_name
GenomicInterval = namedtuple('GenomicInterval', ['chr','strand','start','stop'])

VERBOSE = False
DEBUG_VERBOSE = False
NTHREADS = 1

"""
TODO

Use pysam fasta parser
Use GRIT gene loader
replace get_elements_from_gene with the GRIT elements code

split into:
train from bam, polya-site-seq assay, and GRIT run
predict on just a bam

"""

################################################################################


def reverse_strand( seq ):
    flip = {'a' : 't', 'c' : 'g', 'g' : 'c', 't' : 'a', 'n' : 'c'}
    return ''.join([flip[base] for base in seq[::-1]])


# The upstream and downstream motifs
#
# Retelska et al. BMC Genomics 2006 7:176   doi:10.1186/1471-2164-7-176
use = '''92.1 2.26 1.36 4.26  0
74.72 0.54 4.57 20.14 0
1.76 1.02 3.25 93.94 0
98.43 0.15 1.36 0.04 0
96.67 2.49 0.28 0.55 0
99.46 0.18 0.18 0.17 0'''.split('\n')
#import pdb; pdb.set_trace()
mRNA_LUSE = [ list(map(float,u.split(' '))) for u in use ]
LUSE = [ mRNA[::-1] for mRNA in mRNA_LUSE[::-1] ] 


# Retelska et al. BMC Genomics 2006 7:176   doi:10.1186/1471-2164-7-176 and meme
mRNA_list = ['ataaa', 'attaaa', 'agtaaa', 
             'tataaa', 'aataaa', 'aattaaa', 
             'aagtaaa', 'atataaa']
word_list = []
for word in mRNA_list:
    word_list.append( reverse_strand( word ) ) 



# T G/T G/T T/G G/T G/T C/T
# T T T T G T T 
# Retelska et al. BMC Genomics 2006 7:176   doi:10.1186/1471-2164-7-176
dse = '''8.72 6.62 10.52 74.13 0
1.72 18.64 37.31 42.3 0
4.94 20.65 9.25 65.15 0
1.52 68.43 14.13 15.89 0
8.66 0.15 0.00 91.16 0
0.11 7.63 59.4 32.85 0
9.08 20.42 22.58 47.9 0'''.split('\n')

mRNA_LDSE = [ list(map(float,d.split(' '))) for d in dse ]
LDSE = [ mRNA[::-1] for mRNA in mRNA_LDSE[::-1] ] 


meme_use_cDNA = '''0.718000 0.068000 0.046000 0.168000 0
0.732000 0.074000 0.040000 0.154000 0
0.000000 0.104000 0.020000 0.876000 0
0.762000 0.074000 0.158000 0.006000 0
0.710000 0.160000 0.030000 0.100000 0
0.848000 0.034000 0.104000 0.014000 0
0.262000 0.222000 0.216000 0.300000 0'''.split('\n')

mRNA_MUSE = [ list(map(float,u.split(' '))) for u in meme_use_cDNA ]
MUSE = [ mRNA[::-1] for mRNA in mRNA_MUSE[::-1] ] 
################################################################################



def list_samples( samp_fn ):
    '''
    obtain and organize the list of samples on which to fit the forest
    '''
    all_samples = {}
    fid = open(samp_fn)
    for fn in fid:
        short_name = '.'.join(fn.strip().split('/')[-1].split('.')[:-2])
        very_short_name = short_name.split('.')[0]
        if very_short_name not in all_samples:
            all_samples[very_short_name] = { short_name : [] }
        if short_name not in all_samples[very_short_name]:
            all_samples[very_short_name][short_name] = []
        all_samples[very_short_name][short_name].append(fn.strip())
    for samp in all_samples.keys():
        for rd in all_samples[samp].keys():
            assert len(all_samples[samp][rd]) == 2
    return all_samples

def parse_fasta( fn ):
    '''
    load a fasta file into a dictionary pointing to sinlge strings, one for
    each chromosome
    '''
    genome = dict()
    fid = open(fn)
    chrm = ''
    for line in fid:
        data = line.strip()
        if data.startswith('>'):
            chrm = clean_chr_name(data[1:])
        else:
            if chrm not in genome:
                genome[chrm] = []
                print(chrm, file=sys.stderr)
            genome[chrm].append(data.lower())
    for chrm in list(genome.keys()):
        genome[chrm] = ''.join(genome[chrm])
    fid.close()
    return genome


def polyA_gff_2_dict( fn ):
    '''
    load a polyA gff file into a dictionary object
    chr3L	Read	CDS	17393446	17393502	.	-	.	@HWI-ST382_0049:1:1:4511:58676#CGATGT/1
    '''
    polyA = dict()
    fid = open(fn)
    for line in fid:
        data = line.strip().split('\t')
        chrm = clean_chr_name(data[0])
        strand = data[6]
        if strand == '-':
            p_site = int(data[3])
        else:
            assert strand == '+'
            p_site = int(data[4])
        p_site -= 1 # subtract 1 so that this indexes into the fasta dict object
        if (chrm,strand) not in polyA:
            polyA[(chrm,strand)] = dict()
        if p_site not in polyA[(chrm,strand)]:
            polyA[(chrm,strand)][ p_site ] = 0
        polyA[(chrm,strand)][ p_site ] += 1
    fid.close()
    return polyA

def polyA_dict_2_intersecter( polyA ):
    '''
    load a polyA gff file into a dictionary/intersecter object
    NOTE: bx.python is (open,open) in its interval searchers,
    e.g. T.find( 1,10 ) will not return true if T contains (1,1) or (10,10)
    '''
    polyA_I = dict()
    for (chrm, strand) in list(polyA.keys()):
        if (chrm,strand) not in polyA_I:
            polyA_I[(chrm,strand)] = Intersecter()
        for p_site in polyA[(chrm,strand)]:
            polyA_I[(chrm,strand)].add( 
                p_site, p_site, [p_site, polyA[(chrm,strand)][p_site]] ) 
    return polyA_I
            

def get_elements_from_gene( gene, get_tss=True, get_jns=True, \
                                get_tes=True, get_exons=False ):
    tss_exons = set()
    tes_exons = set()
    introns = set()
    exons = set()
    
    chrm, strand = clean_chr_name(gene.chrm), gene.strand
    transcripts = gene.transcripts
    
    for trans in transcripts:
        bndries = trans.exon_bnds

        fp_region = GenomicInterval(chrm, strand, bndries[0], bndries[1])
        tp_region = GenomicInterval(chrm, strand, bndries[-2], bndries[-1])
        if strand == '+':
            if get_tss:
                tss_exons.add( fp_region )
            if get_tes:
                tes_exons.add( tp_region )
        else:
            if strand != '-':
                print("BADBADBAD", strand, file=sys.stderr)
                continue
            assert strand == '-'
            if get_tss:
                tss_exons.add( tp_region )
            if get_tes:
                tes_exons.add( fp_region )
        
        if get_jns:
            for start, stop in zip( bndries[1:-2:2], bndries[2:-1:2] ):
                # add and subtract 1 to ge tthe inclusive intron boundaries,
                # rather than the exon boundaries
                if start >= stop:
                    continue
                introns.add( GenomicInterval(chrm, strand, start+1, stop-1) )

        if get_exons:
            for start, stop in zip( bndries[::2], bndries[1::2] ):
                exons.add( GenomicInterval(chrm, strand, start, stop) )
    
    return tss_exons, introns, tes_exons, exons

def get_element_sets( genes, get_tss=True, get_jns=True, \
                          get_tes=True, get_exons=True ):
    tss_exons = set()
    introns = set()
    tes_exons = set()
    exons = set()
    for gene in genes:
        i_tss_exons, i_introns, i_tes_exons, i_exons = \
            get_elements_from_gene( gene, get_tss, get_jns, get_tes, get_exons )
        
        tss_exons.update( i_tss_exons )
        introns.update( i_introns  )
        tes_exons.update( i_tes_exons )
        exons.update( i_exons )

    return sorted(tss_exons),sorted(introns),sorted( exons ),sorted(tes_exons)


                      
def gtf_2_intersecters_and_dicts( gtf_fname ):
    '''
    parse a gtf file into two intersecters: CDSs and introns
    use the fast_gtf parser to get introns, and get CDSs via brute force
    (I realize this is incredibly stupid, but don't want to muck up 
    intron boundaries)
    '''
    # get the Intron intersecter and interval objects
    def GenomicInterval_2_intersecter_and_dict( GI ):
        II = dict()
        ID = dict()
        for intron in GI:
            chrm = clean_chr_name(intron.chr)
            strand = intron.strand
            if ( chrm, strand ) not in II:
                II[ (chrm,strand) ] = Intersecter()
            II[ (chrm,strand) ].add( intron.start,intron.stop, 
                                     [ intron.start,intron.stop ] )
            if ( chrm, intron.strand ) not in ID:
                ID[ (chrm,strand) ] = []
            ID[ (chrm,strand) ].append( [intron.start,intron.stop] )  
        return II, ID

    # get the CDS intersecters and interval objects
    def gtf_CDSs_2_intersecter_and_dict( gtf_fn ):
        fid = open(gtf_fname)
        CDS_I = dict()    
        CDS_D = dict()    
        for line in fid:
            data = line.strip().split('\t')
            if not data[2] == 'CDS':
                continue
            chrm = clean_chr_name(data[0])
            strand = data[6]
            start = int(data[3])
            end = int(data[4])
            if (chrm,strand) not in CDS_I:
                CDS_I[ (chrm,strand) ] = Intersecter()
            CDS_I[ (chrm,strand) ].add( start,end, [start,end] )
            if (chrm,strand) not in CDS_D:
                CDS_D[ (chrm,strand) ] = []
            CDS_D[ (chrm,strand) ].append( [start,end] )
        return CDS_I, CDS_D
            

    # load the genes and build sorted, unique lists
    genes = load_gtf( gtf_fname )
    tss_exons, introns, exons, tes_exons = get_element_sets( \
        genes, True, True, True, True )

    # generate all the intersecters and intervals for the annotation
    Introns_Sect, Introns_Dict = \
        GenomicInterval_2_intersecter_and_dict( introns )
    Exons_Sect, Exons_Dict = GenomicInterval_2_intersecter_and_dict( exons )
    CDSs_Sect, CDSs_Dict = gtf_CDSs_2_intersecter_and_dict( gtf_fname ) 
    return ( Introns_Sect, Introns_Dict, 
             Exons_Sect, Exons_Dict, 
             CDSs_Sect, CDSs_Dict )



def purify_introns( Introns_Dict, Exons_Sect ):
    '''
    select only introns that don't contain exons, these are likely to be "pure"
    introns that lack any real polyA sites.
    '''
    pure = dict()
    for (chrm, strand) in list(Introns_Dict.keys()):
        for intron in Introns_Dict[(chrm, strand)]:
            if not Exons_Sect[(chrm, strand)].find( intron[0], intron[1] ):
                if (chrm, strand) not in pure:
                    pure[(chrm, strand)] = Intersecter()
                pure[(chrm, strand)].add( intron[0], intron[1], intron )
    return pure


def get_overlapping_elements( tes_dict, elements_I, w ):
    '''
    Find all elements (tes's) overlapping another element type
    '''
    start = w
    end = w+1
    over = dict()
    for (chrm,strand) in list(tes_dict.keys()):
        if (chrm,strand) not in elements_I:
            print("warning, element_intersecter does not contain the chrm: ", chrm, file=sys.stderr)
            continue
        for tes in list(tes_dict[(chrm,strand)].keys()):
            H = elements_I[(chrm,strand)].find(tes-start,tes+end)
            if H:
                if (chrm,strand) not in over:
                    over[ (chrm,strand) ] = dict()
                over[ (chrm,strand) ][tes] = copy.deepcopy( 
                    tes_dict[ (chrm,strand) ][tes] )
    return over


def remove_overlapping_elements( tes_dict, elements_I, w ):
    '''
    Remove all elements (tes's) overlapping another element type
    '''
    start = w
    end = w+1
    over = dict()
    for (chrm,strand) in list(tes_dict.keys()):
        if (chrm,strand) not in elements_I:
            print("warning, element_intersecter does not contain the chrm: ", chrm, file=sys.stderr)
            continue
        for tes in list(tes_dict[(chrm,strand)].keys()):
            H = elements_I[(chrm,strand)].find(tes-start,tes+end)
            if not H:
                if (chrm,strand) not in over:
                    over[ (chrm,strand) ] = dict()
                over[ (chrm,strand) ][tes] = copy.deepcopy( 
                    tes_dict[ (chrm,strand) ][tes] )
    return over



def extract_genome_sequence( genome, tes_dict, w ):
    '''
    Return an array of sequences each of size 2*w + 1 
    '''
    seqs = []
    start = w
    end = w+1
    for (chrm,strand) in list(tes_dict.keys()):
        if chrm not in genome:
            print("warning, genome sequence does not contain the chrm: ", chrm, file=sys.stderr)
            continue
        for tes in list(tes_dict[(chrm,strand)].keys()):
            seq = genome[chrm][tes-start:tes+end]
            if strand == "-":
                seqs.append([[chrm,strand,tes,tes_dict[(chrm,strand)][tes]], 
                             reverse_strand(seq)])
            else:
                assert strand == "+"
                seqs.append([[chrm,strand,tes,tes_dict[(chrm,strand)][tes]], 
                             seq])
    return seqs



def find_indexes_of_word( word, seq ):
    '''
    find each location where a given word occurs in a sequcence
    '''
    all_indexes = set()
    curr_index = seq.find(word)
    L = len(seq)
    while curr_index >= 0:
        all_indexes.add(curr_index)
        curr_index = seq.find(word, curr_index+1, L)
    return all_indexes

def seq_2_index( seq ):
    '''
    Turn a DNA sequence into indicies 0-4 for speedy motif searches
    '''
    ind = {'a' : 0, 'c' : 1, 'g' : 2, 't' : 3, 'n' : 4}
    sind = [ ind[s] for s in seq[1] ]
    return sind


def search_for_motif( seq_ind, motif ):
    '''
    Do a reasonably speedy motif search of a sequence, return the vector of 
    scores
    '''
    L = len(motif)
    ls = len(seq_ind)
    scores = []
    for i in range( 0, ls - L ):
        curr_score = 0
        for j,m in enumerate(motif):
            curr_score += m[seq_ind[i+j]]
        scores.append(curr_score)
    return numpy.asarray(scores)



def extract_covariates_from_seqs( seqs, w, polyA_density_curr, 
                                  RNA_density, RNA_header ):
    '''
    All the heavy lifting is done here, a massive function to get all the
    covariates that turn out to be important.  
    '''

    # initilize point names:
    all_points = []


    # here is the massive header of covariates that will be generated in this fn
    # note that RNA-seq and polyA (local density) covariates will be appended.
    header = ['name','read_count','triplet_ID-1','triplet_ID_center','triplet_ID+1', 'reads_within_10bp','reads_within_20bp','reads_within_50bp',
        'count_ATAAA_20_40', 'dist_ATAAA_20', 'dist_ATAAA_40', 'count_ATAAA_0_20', 'loc_ATAAA_0_20', 
        'count_ATTAAA_20_40', 'dist_ATTAAA_20', 'dist_ATTAAA_40', 'count_ATTAAA_0_20', 'loc_ATTAAA_0_20',  
        'count_AGTAAA_20_40', 'dist_AGTAAA_20', 'dist_AGTAAA_40', 'count_AGTAAA_0_20', 'loc_AGTAAA_0_20', 
        'count_TATAAA_20_40', 'dist_TATAAA_20', 'dist_TATAAA_40', 'count_TATAAA_0_20', 'loc_TATAAA_0_20',
        'count_AATAAA_20_40', 'dist_AATAAA_20', 'dist_AATAAA_40', 'count_AATAAA_0_20', 'loc_AATAAA_0_20',
        'count_AATTAAA_20_40', 'dist_AATTAAA_20', 'dist_AATTAAA_40', 'count_AATTAAA_0_20', 'loc_AATTAAA_0_20',
        'count_AAGTAAA_20_40', 'dist_AAGTAAA_20', 'dist_AAGTAAA_40', 'count_AAGTAAA_0_20', 'loc_AAGTAAA_0_20',
        'count_ATATAAA_20_40', 'dist_ATATAAA_20', 'dist_ATATAAA_40', 'count_ATATAAA_0_20', 'loc_ATATAAA_0_20',
        'word_total_USE_counts_20_40','word_total_USE_counts_0_20',
        'mx_score_U_20_40', 'loc_mx_U_20_40', 'mx_score_mU_20_40', 'loc_mx_mU_20_40', 
        'mx_score_D_55_80', 'loc_mx_D_55_80', 'mx_score_D_80_100', 'loc_mx_D_80_100', 
        'sum_D_55_80', 'sum_D_80_100']

    # extend the header to include the RNA-seq covariates.
    header.extend(RNA_header)

    # Initialize the "Big X", the set of covariates, the predictor matrix
    Big_X = []

    # turn all the sequences into the indices 0-4
    seqs_inds = []
    for seq in seqs:
        seqs_inds.append( seq_2_index( seq ) )

    delete_this = []
    for ind,seq in enumerate(seqs):
        Big_X.append([])
        if len(seq[1]) < 101:
            key_code = '_'.join(map(str,seq_index[:-1]))
            local_density = polyA_density_curr[key_code]
            delete_this.append(ind)
            print(w, seq, local_density, file=sys.stderr)
            continue
            
        seq_ind = seqs_inds[ind]
        seq_index = seq[0]
        chrm = clean_chr_name(seq_index[0])
        sequence_name = '_'.join(map(str,seq_index))
        all_points.append(sequence_name)

        ##### Add a covariate ##################################################
        # get the local read count #############################################
        Big_X[ind].extend( [seq_index[-1]] )
        
        seq = seq[1] # don't need the positional information any more.

        ##### Add a covariate ##################################################
        # encode the letter triplet at the polyA site itself ###################
        Big_X[ind].extend( seq_ind[40:60] ) ####################################

        ##### Add a covariate ##################################################
        # get the local density ################################################
        key_code = '_'.join(map(str,seq_index[:-1])) ###########################
        local_density = polyA_density_curr[key_code] ###########################
        Big_X[ind].extend( local_density )

        # search for all the words. NOTE: These are currently all upstream 
        # elements
        total_count = 0
        total_count_0_20 = 0
        word_cov = []
        for word in word_list:
            all_occur = numpy.asarray(list(find_indexes_of_word( word, seq[19:40] )))
            # select covariates from the word occurrence locations
            # number occur in 20-40, location nearest 20, location nearest 40
            number = len(all_occur)
            nearest_20 = 0
            nearest_40 = 0
            if number > 0:
                nearest_20 = min( all_occur )
                nearest_40 = max( all_occur )
            occur_first_20 = numpy.asarray(list(find_indexes_of_word( word, seq[0:20] )))
            number_first_20 = len( occur_first_20 )
            total_count_0_20 += number_first_20
            mx_20 = 0
            if number_first_20 > 0:
                mx_20 = max( occur_first_20 )
            total_count += number
            word_cov.append( [number, nearest_20, nearest_40, number_first_20, mx_20] )

            ##### Add a covariate ##############################################
            # Add the position and counts of word occurances ###################
            Big_X[ind].extend( [number, nearest_20, nearest_40, number_first_20, mx_20] )

        ##### Add a covariate ##################################################
        # Add the total counts of word occurences ##############################
        Big_X[ind].extend( [total_count,total_count_0_20] )

            
        # do all the motif searchs
        U_20_40 = search_for_motif( seq_ind[19:40], LUSE )
        mU_20_40 = search_for_motif( seq_ind[19:40], MUSE )
        D_55_80 = search_for_motif( seq_ind[54:80], LDSE )
        D_80_100 = search_for_motif( seq_ind[79:100], LDSE )

        # get the motif-based covariates
        mx_U_20_40 = U_20_40.max()
        loc_mx_U_20_40 = U_20_40.argmax()
        mx_mU_20_40 = mU_20_40.max()
        loc_mx_mU_20_40 = mU_20_40.argmax()
        mx_D_55_80 = D_55_80.max()
        loc_mx_D_55_80 = D_55_80.argmax()
        mx_D_80_100 = D_80_100.max()
        loc_mx_D_80_100 = D_80_100.argmax()
        D_sum_55_80 = D_55_80.sum() # similarity of nucleotide frequencies
        D_sum_80_100 = D_80_100.sum() # similarity of nucleotide frequencies


        ##### Add a covariate ##################################################
        # add all the motif-related covariates #################################
        Big_X[ind].extend( [mx_U_20_40, loc_mx_U_20_40, mx_mU_20_40, 
                            loc_mx_mU_20_40, mx_D_55_80, loc_mx_D_55_80, 
                            mx_D_80_100, loc_mx_D_80_100, 
                            D_sum_55_80, D_sum_80_100] )


        # get the RNA densities 
        try:
            local_RNA = RNA_density[key_code]
        except:
            import pdb; pdb.set_trace()

        ##### Add a covariate ##################################################
        # add all the RNA-seq related covariates ###############################
        Big_X[ind].extend( local_RNA )

        Big_X[ind] = numpy.asarray(Big_X[ind])

        #if not len(Big_X[ind]) == len(header)-1:
        #    import pdb; pdb.set_trace()
    if len(delete_this) > 0:
        del Big_X[numpy.asarray(delete_this)]
    return numpy.asarray(Big_X), header, all_points


def get_local_read_density(polyA_reads_D, polyA_reads_I):
    '''
    get the local polyA read density.
    '''
    seq_dict = dict()
    for (chrm,strand) in list(polyA_reads_D.keys()):
        for pos in list(polyA_reads_D[(chrm,strand)].keys()):
            # e.g. 'key' will end up looking like: chr2L_+_2030538
            key = '_'.join([chrm,strand,str(pos)])
            seq_dict[key] = [ len( polyA_reads_I[(chrm,strand)].find(pos-10,pos+11) ),
                len( polyA_reads_I[(chrm,strand)].find(pos-20,pos+21) ),
                len( polyA_reads_I[(chrm,strand)].find(pos-50,pos+51) ) ]
    return seq_dict

def get_predictors_for_polya_site( reads, chrm, strand, pos ):
    rd1_cvg = reads.build_read_coverage_array(
        chrm, strand, max(0,pos-100), pos+100, read_pair=1 )
    rd2_cvg = reads.build_read_coverage_array(
        chrm, strand, max(0,pos-100), pos+100, read_pair=2 )
    # if we can't get the full read coverage, this doesn't make
    # sense so skip this polya
    if len(rd1_cvg) != 201: return None
    if len(rd2_cvg) != 201: return None

    ### TODO - BEN - can't we just reverse rd1_cvg ( ie, 
    # if strand == '-': rd1_cvg = rd1_cvg[::-1] ) and skip
    # the strand special casing

    upstream_10_rd1 = rd1_cvg[100-10:100].sum()
    downstream_10_rd1 = rd1_cvg[100:100+10].sum()

    upstream_50_rd1 = rd1_cvg[100-50:100].sum()
    downstream_50_rd1 = rd1_cvg[100:100+50].sum()

    upstream_100_rd1 = rd1_cvg[100-100:100].sum()
    downstream_100_rd1 = rd1_cvg[100:100+100].sum()

    upstream_10_rd2 = rd2_cvg[100-10:100].sum()
    downstream_10_rd2 = rd2_cvg[100:100+10].sum()

    upstream_50_rd2 = rd2_cvg[100-50:100].sum()
    downstream_50_rd2 = rd2_cvg[100:100+50].sum()

    upstream_100_rd2 = rd2_cvg[100-100:100].sum()
    downstream_100_rd2 = rd2_cvg[100:100+100].sum()

    if strand == '+':
        return [
            upstream_10_rd1, downstream_10_rd1,
            upstream_50_rd1, downstream_50_rd1,
            upstream_100_rd1, downstream_100_rd1, 
            upstream_10_rd1/max(downstream_10_rd1,1), 
            upstream_50_rd1/max(downstream_50_rd1,1), 
            upstream_100_rd1/max(downstream_100_rd1,1),
            upstream_10_rd2, downstream_10_rd2,
            upstream_50_rd2, downstream_50_rd2,
            upstream_100_rd2, downstream_100_rd2, 
            upstream_10_rd2/max(downstream_10_rd2,1), 
            upstream_50_rd2/max(downstream_50_rd2,1), 
            upstream_100_rd2/max(downstream_100_rd2,1),
            upstream_10_rd1/max(downstream_10_rd2,1), 
            upstream_50_rd1/max(downstream_50_rd2,1), 
            upstream_100_rd1/max(downstream_100_rd2,1)
            ]
    else:
        return [
            downstream_10_rd1, upstream_10_rd1,
            downstream_50_rd1, upstream_50_rd1,
            downstream_100_rd1, upstream_100_rd1, 
            downstream_10_rd1/max(upstream_10_rd1,1), 
            downstream_50_rd1/max(upstream_50_rd1,1),
            downstream_100_rd1/max(upstream_100_rd1,1),
            downstream_10_rd2, upstream_10_rd2,
            downstream_50_rd2, upstream_50_rd2,
            downstream_100_rd2, upstream_100_rd2, 
            downstream_10_rd2/max(upstream_10_rd2,1), 
            downstream_50_rd2/max(upstream_50_rd2,1), 
            downstream_100_rd2/max(upstream_100_rd2,1),
            downstream_10_rd1/max(upstream_10_rd2,1), 
            downstream_50_rd1/max(upstream_50_rd2,1), 
            downstream_100_rd1/max(upstream_100_rd2,1)
            ]
    assert False

def get_RNAseq_density_worker( reads, sites, sites_lock, dense ):
    while True:
        with sites_lock:
            sites_len = len( sites )
            if sites_len == 0: break
            # using the commented out code appears slower because
            # some regions ( like M ) have so many reads, that them
            # all getting stuck in 1 group outweighs the lock overhead
            # of doing 1 at a time. A random sort might fix this, but it
            # seems fast enough as is. 
            args = [sites.pop(),] #[-1:]
            #del sites[-1:]
        if DEBUG_VERBOSE and sites_len%1000 == 0:
            print("%i polyA sites remain" % sites_len, file=sys.stderr)
        for chrm, strand, pos, cnt in args:
            key = '_'.join([chrm,strand,str(pos)])
            predictors = get_predictors_for_polya_site( 
                reads, chrm, strand, pos )
            if key not in dense:
                dense[key] = predictors
            else:
                dense[key] = dense[key] + predictors

    return

def get_RNAseq_densities( all_reads, polyAs ):
    '''
    get the local RNA-seq read densities 
    '''
    dense = dict()
    header = []
    for sample in (x.filename for x in all_reads):
        header.extend( [ sample + '_up_10_rd1', 
                         sample + 'down_10_rd1', 
                         sample + '_up_50_rd1', 
                         sample + 'down_50_rd1', 
                         sample + '_up_100_rd1', 
                         sample + 'down_100_rd1', 
                         sample + '_up_down_rat_10_rd1',
                         sample + '_up_down_rat_50_rd1', 
                         sample + '_up_down_rat_100_rd1' ] )
        header.extend( [ sample + '_up_10_rd2', 
                         sample + 'down_10_rd2', 
                         sample + '_up_50_rd2', 
                         sample + 'down_50_rd2', 
                         sample + '_up_100_rd2', 
                         sample + 'down_100_rd2', 
                         sample + '_up_down_rat_10_rd2', 
                         sample + '_up_down_rat_50_rd2', 
                         sample + '_up_down_rat_100_rd2' ] )
        header.extend( [ sample + '_up_down_rat_10_rd1_rd2', 
                         sample + '_up_down_rat_50_rd1_rd2', 
                         sample + '_up_down_rat_100_rd1_rd2' ] )
    
    # process a list of arguments for multithreading
    import multiprocessing
    manager = multiprocessing.Manager()
    dense = manager.dict()
    sites = manager.list()
    sites_lock = manager.Lock()
    
    for reads in all_reads:
        for (chrm, strand), polyA in polyAs.items():
            chrm = clean_chr_name( chrm )
            for pos, cnt in sorted(polyA.items()):
                sites.append( (chrm, strand, pos, cnt) )
    
    if VERBOSE: 
        print("Finding RNASeq read coverage around poly(A) sites with %i threads"\
                % NTHREADS, file=sys.stderr)
    if NTHREADS == 1:
        get_RNAseq_density_worker( reads, sites, sites_lock, dense )
    else:
        from lib.multiprocessing_utils import Pool
        all_args = [( reads, sites, sites_lock, dense )]*NTHREADS
        p = Pool(NTHREADS)
        p.apply( get_RNAseq_density_worker, all_args )
    
    if VERBOSE: print("FINISHED finding poly(A) coverage")
    
    return dict(dense), header


def print_bed_from_D( D ):
    for (chrm,strand) in list(D.keys()):
        for pos in list(D[(chrm,strand)].keys()):
            print('\t'.join( map(str,[chrm, pos, pos+1, strand, D[(chrm,strand)][pos]]) ))


def print_fasta_from_seq( seq, out_fn, ind_start, ind_end ):
    fid = open(out_fn,'w')
    for line in seq:
        print('>' + '_'.join(map(str,line[0])), file=fid)
        print(line[1][ind_start:ind_end], file=fid)
    return


def fit_forests( X_pos, X_neg_set, total_sets, size_train, size_test ):
    '''
    X_pos -- a numpy matrix.

    X_neg_set -- an array of numpy matrices corresponding the various negative
    control datasets that will be used to fit the forest, e.g. CDSs and Introns.
    
    total_sets -- the number of RFs to fit. An ensemble of ensembles is used 
    because the training data is not as larger or diverse as one might like.
    This is good for making sure that each classifier is not overfit while at 
    the same time utilizing all of the data for training.

    size_train -- a vector of training set sizes.  The first entry is the number
    of positive examples that will be drawn from X_pos, and remainder are for 
    the negs and should be in the same order as X_neg_set.

    size_test -- same as size_train but for the test set. 

    clf = RandomForestClassifier(n_estimators=10)
    sklearn.ensemble.RandomForestClassifier(n_estimators=10, 
    criterion='gini', max_depth=None, min_samples_split=1, min_samples_leaf=1, 
    min_density=0.1, max_features='auto', bootstrap=True, 
    compute_importances=False, 
    oob_score=False, n_jobs=1, random_state=None, verbose=0)
    '''

    # compute the sizes of the input datasets
    Lp = len(X_pos)
    Ln = []
    for X_neg in X_neg_set:
        Ln.append( len(X_neg) )

    # initilize the ensemble of ensembles
    Forests = []
    # store the error information
    Errs = []

    # build the labels for the training and test data
    train_labels = numpy.zeros(sum(size_train))
    train_labels[:size_train[0]] += 1
    test_labels = numpy.zeros(sum(size_test))
    test_labels[:size_test[0]] += 1
    for j in range( 0, total_sets ):
        t1 = time.time()
        # do the randomization to select the training and test sets
        pos_perm = numpy.random.permutation( Lp )
        neg_perm_set = []
        for L in Ln:
            neg_perm_set.append( numpy.random.permutation( L ) )

        # build the training data
        X_train = list( X_pos[ pos_perm[:size_train[0]] ] )
        for i,X_neg in enumerate(X_neg_set):
            X_neg_train = list( X_neg[ neg_perm_set[i][:size_train[i+1]] ] )
            X_train.extend(X_neg_train)
        X_train = numpy.asarray(X_train)

        # build the test data
        top = size_train[0]+size_test[0]
        X_test = list( X_pos[ pos_perm[size_train[0]:top] ] )
        for i,X_neg in enumerate(X_neg_set):
            top = size_test[i+1]+size_train[i+1]
            X_neg_test = list( X_neg[ neg_perm_set[i][size_train[i+1]:top] ] )
            X_test.extend(X_neg_test)
        X_test = numpy.asarray(X_test)

        # initilize and fit the forest
        Forests.append(RandomForestClassifier(n_estimators=100,max_features=80))
        Forests[j].fit( X_train, train_labels )
        
        # test the forest on the held-out test data
        test = Forests[j].predict( X_test )
        FN = sum(test[:size_test[0]]==0)/float(size_test[0])
        FP = sum(test[size_test[0]:]==1)/float(sum(size_test[1:]))
        Errs.append([FN,FP])
        print(FN, FP, time.time()-t1, file=sys.stderr)
    import pdb; pdb.set_trace()
    return Forests, Errs


def parse_arguments():
    import argparse

    parser = argparse.ArgumentParser(\
        description='Find the poly(A) sites expressed from an RNAseq experiment.')

    parser.add_argument( 
        '--rnaseq-reads', type=argparse.FileType('rb'), required=True, 
        help='BAM file containing mapped RNAseq reads.')
    parser.add_argument( '--rnaseq-read-type', required=True,
        choices=["forward", "backward"],
        help='Whether or not the first RNAseq read in a pair needs to be reversed to be on the correct strand.')
    
    parser.add_argument( '--fasta', type=file, required=True,
                         help='Fasta file containing the genome sequence')
    parser.add_argument( '--reference', type=file, required=True,
                         help='Reference GTF')
    parser.add_argument( '--polya-reads', type=file, required=True,
                         help='BAM file containing mapped polya reads.')
    parser.add_argument( '--true-positive-tes', type=file, required=True,
                         help='GTF file containing a verified set of TES.')
        
    parser.add_argument( '--verbose', '-v', default=False, action='store_true',
                         help='Whether or not to print status information.')
    parser.add_argument( '--threads', '-t', default=1, type=int,
                         help='The number of threads to use.')
        
    args = parser.parse_args()

    global VERBOSE
    VERBOSE = args.verbose
    global NTHREADS
    NTHREADS = args.threads
    
    ret_files = ( args.fasta, args.reference, args.polya_reads,
                  args.true_positive_tes, args.rnaseq_reads )
    for fp in ret_files: fp.close()
    return [ fp.name for fp in ret_files ]


def fit_forest(rnaseq_reads, polya_reads, true_polya_regions):
    pass

def predict_active_polya_sites(forest, rnaseq_reads, candidate_polya_sites):
    pass


def main():
    ( genome_fname, annotation_fname, polyA_reads_fname, cDNA_tes_fname, 
      rnaseq_bam_fname ) = parse_arguments()
    reads = RNAseqReads( rnaseq_bam_fname ).init(reverse_read_strand=True)

    # load in the polyA reads
    if VERBOSE: print("Loading poly(A) reads", file=sys.stderr)
    polyA_reads_D = polyA_gff_2_dict( polyA_reads_fname )
    polyA_reads_I = polyA_dict_2_intersecter( polyA_reads_D )

    if VERBOSE: print("Loading RNAseq densities", file=sys.stderr)
    RNA_dense, RNA_header = get_RNAseq_densities( [reads,], polyA_reads_D )
    
    #import pdb; pdb.set_trace()
       
    # set the size of the window we will extract
    window = 50
    
    # get local read density
    if VERBOSE: print("Finding local read density", file=sys.stderr)
    polyA_density = get_local_read_density(polyA_reads_D, polyA_reads_I)

    # load in the reference GTF
    if VERBOSE: print("Loading reference GTF", file=sys.stderr)
    Introns_Sect, Introns_Dict, Exons_Sect, Exons_Dict, CDSs_Sect, CDSs_Dict = (
        gtf_2_intersecters_and_dicts( annotation_fname ) )

    # load in the cDNA polyA ends
    if VERBOSE: print("Loading Gold polyA sites", file=sys.stderr)
    cDNA_polyA_D = polyA_gff_2_dict( cDNA_tes_fname )
    cDNA_polyA_I = polyA_dict_2_intersecter( cDNA_polyA_D )
    #cDNA_density = get_local_read_density(cDNA_polyA_D, cDNA_polyA_I)

    # purify cDNAs to remove those that overlap CDSs:
    if VERBOSE: print("Filtering Gold polyAs that overlap CDSs", file=sys.stderr)
    cDNA_polyA_noCDS_D = remove_overlapping_elements( 
            cDNA_polyA_D, CDSs_Sect, window )

    if VERBOSE: print("Find polyAs that intersect gold set", file=sys.stderr)
    # get a set of "positive", polyA reads that we believe
    polyA_reads_cDNA_ends_D = get_overlapping_elements( 
            polyA_reads_D, cDNA_polyA_I, window )
    # purify "positives" to remove those that overlap CDSs:
    polyA_reads_cDNA_noCDS_D = remove_overlapping_elements( 
            polyA_reads_cDNA_ends_D, CDSs_Sect, window )

    # load in the reference genome indexed by chrm
    if VERBOSE: print("Loading reference genome", file=sys.stderr)
    FA = parse_fasta( genome_fname )

    if VERBOSE: print("Extracting reference sequence", file=sys.stderr)
    # extract genome sequences around cDNA polyA ends that don't overlap CDSs
    cDNA_polyA_noCDS_seqs = extract_genome_sequence( 
            FA, cDNA_polyA_noCDS_D, window )

    # extract genome sequences around cDNA polyA ends that don't overlap CDSs
    polyA_reads_cDNA_noCDS_seqs = extract_genome_sequence( 
            FA, polyA_reads_cDNA_noCDS_D, window )
    X_polyA_cDNA, header,point_names_polyA_cDNA = extract_covariates_from_seqs( 
            polyA_reads_cDNA_noCDS_seqs, 50, polyA_density,RNA_dense,RNA_header)

    if VERBOSE: print("Finding negative polya set", file=sys.stderr)
    # 1.a) get a set of introns that overlap no exons, TESs in these 
    # should be largely rubbish
    pure_introns_I = purify_introns( Introns_Dict, Exons_Sect )

    # 1.b) find the polyA reads that fall in these "pure" introns
    polyA_intronic_reads_D = get_overlapping_elements( 
            polyA_reads_D, pure_introns_I, 0 )
    
    # find the polyA reads that fall in CDSs
    polyA_CDS_reads_D = get_overlapping_elements( polyA_reads_D, CDSs_Sect, 0 )

    if VERBOSE: print("Finding negative polya's genome sequence", file=sys.stderr)
    # extract sequences corresponding to negatives
    polyA_CDS_reads_seqs = extract_genome_sequence( 
            FA, polyA_CDS_reads_D, window )
    X_polyA_CDS, header, point_names_CDS = extract_covariates_from_seqs( 
            polyA_CDS_reads_seqs, 50, polyA_density, RNA_dense, RNA_header )
    polyA_intronic_reads_seqs = extract_genome_sequence( 
            FA, polyA_intronic_reads_D, window )
    X_polyA_intronic,header,point_names_intronic = extract_covariates_from_seqs(
            polyA_intronic_reads_seqs, 50, polyA_density, RNA_dense, RNA_header)

    import pdb; pdb.set_trace()

    # fit the forests:
    if VERBOSE: print("Fitting the random forest", file=sys.stderr)
    Forests, Errs = fit_forests( X_polyA_cDNA, [X_polyA_CDS, X_polyA_intronic], 
                                 3, [2000, 2000, 800], [2000, 2000, 800] )

    # get all polyA seqs
    polyA_reads_seqs = extract_genome_sequence( FA, polyA_reads_D, window )
    X_polyA_all, header, point_names_all = extract_covariates_from_seqs(
            polyA_reads_seqs, 100, polyA_density, RNA_dense, RNA_header )

    # do all the predictions for each forest
    if VERBOSE: print("Predicting from forest", file=sys.stderr)
    preds = []
    L = len(Forests)
    fl = 1
    for forest in Forests:
        preds.append( forest.predict(X_polyA_all) )
    all_preds = numpy.zeros(len(preds[0]))
    for i in range(0,len(preds[0])):
        curr_pred = 0
        for j in range(0,L):
            curr_pred += preds[j][i]
        if curr_pred > fl:
            all_preds[i] = 1


    if VERBOSE: print("Aggregatong and writing good polya to output file", file=sys.stderr)
    # collect all the polyA ends that pass prediction
    every_site = {}
    for i,p in enumerate(all_preds):
        if p == 1:
            every_site[point_names_all[i]] = X_polyA_all[i][0]
    # add on all the polyA ends from cDNAs
    #for (chrm,strand) in cDNA_polyA_noCDS_D.iterkeys():
    #    for pos in cDNA_polyA_noCDS_D[(chrm,strand)]:
    #        key_code = '_'.join([chrm,strand,str(pos)])
    #        if not every_site.has_key(key_code):
    #            every_site[key_code] = 10.5


    # print out bedGraphs of all clean polyA ends
    posfid = open('clean_454_polyA_sites_above_10.plus.bedGraph','w')
    minfid = open('clean_454_polyA_sites_above_10.minus.bedGraph','w')
    for key in every_site.keys():
        data = key.split('_')
        chrm = data[0]
        strand = data[1]
        pos = data[2]
        score = str(every_site[key])
        if strand == '+':
            print('\t'.join( [chrm, pos, pos, score] ), file=posfid)
        else:
            print('\t'.join( [chrm, pos, pos, score] ), file=minfid)
        
    posfid.close()
    minfid.close()

    # Pickle Forest using protocol 0.
    #pkl_forest_fid = open('pickled_forest_antiCDS_thin.pkl', 'wb')
    #pickle.dump([Forests, Errs, header, all_samples, all_preds, every_site], pkl_forest_fid)
    #pkl_forest_fid.close()


    # Pickel the covariates in case we want to try to re-fit without having to bloody reload everything.
    #pkl_cov_fid = open('pickled_cov_antiCDS_thin.pkl', 'wb')
    #pickle.dump([X_polyA_cDNA, X_polyA_CDS, X_polyA_intronic, X_polyA_all, header, [5, [1000, 1500, 800], [1000, 1500, 800]]], pkl_cov_fid)
    #pkl_cov_fid.close()

    import pdb; pdb.set_trace()
    # gets 85% of FlyBase r5.45 3' ends, and maintains 13,865 intergenic polyA sites 

if __name__ == '__main__':
    main()
