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

COMP_BASES = { 'A':'T', 'T':'A', 'C':'G', 'G':'C' }

def find_coding_sequence( tran, fa ):
    res = []
    for start, stop in tran.cds_exons:
        res.append( fa.fetch( 'chr' + tran.chrm, start-1, stop-1+1 ) )
        assert len( res[-1] ) == stop - start + 1
    dna = "".join( res ).upper()
    if len(dna)%3 != 0:
        dna = dna[:-(len(dna)%3)]
    if tran.strand == '-':
        dna = "".join( COMP_BASES[x] for x in dna[::-1] )
    return "".join( GENCODE[dna[i:i+3].upper()] for i in range(0,len(dna),3) )

def format_into_80_char_lines( x ):
    res = []
    for i in range((len(x)//80)+1):
        res.append( x[i*80:(i+1)*80] )
    return res
