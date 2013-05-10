import os, sys

sys.path.append( os.path.join(os.path.dirname(__file__), "../file_types/") )
from gtf_file import load_gtf
VERBOSE = False
import psycopg2

def add_exons_to_db( transcript, conn ):
    cursor = conn.cursor()
    for (start, stop) in transcript.exons:
        query = "INSERT INTO annotations.exons " \
            + "( transcript, location )" \
            + "VALUES ( %s, %s)"
        cursor.execute( query, (transcript.id, '[%s, %s]' % (start, stop) ) )
    cursor.close()
    return

def add_transcript_regions_to_db( transcript, conn ):
    cursor = conn.cursor()
    # add the coding sequence
    if transcript.is_protein_coding:
        start = transcript.relative_pos( transcript.cds_region[0] )
        stop = transcript.relative_pos( transcript.cds_region[1] )
        query = "INSERT into annotations.transcript_regions " \
              + "VALUES ( %s, %s, %s )"
        cursor.execute( query, (transcript.id, '[%s, %s]' % (start, stop), 'CDS') )
    
    cursor.close()
    return

def add_transcript_to_db( gene_id, (chrm, assembly_id), transcript, conn ):
    assert chrm == transcript.chrm
    cursor = conn.cursor()
    query = "INSERT INTO annotations.transcripts " \
        + "( id, gene, contig, strand, location )" \
        + "VALUES (%s, %s, %s, %s, %s)"
    args = ( transcript.id, gene_id, 
             '(\"%s\", %s)' % (chrm, assembly_id),
             transcript.strand, 
             '[%s, %s]' % (transcript.start, transcript.stop) )

    cursor.execute( query, args )
    cursor.close()
    
    add_exons_to_db( transcript, conn )
    
    add_transcript_regions_to_db( transcript, conn )
    
    return 

def add_gene_to_db( gene, annotation_key, assembly_id, conn, 
                    use_name_instead_of_id=True ):
    cursor = conn.cursor()
    
    if use_name_instead_of_id and 'gene_name' in gene.meta_data:
        gene_name = gene.meta_data['gene_name']
    else:
        gene_name = gene.id
    
    #add the gene entry
    query = "INSERT INTO annotations.genes " \
        + "( name, annotation, contig, strand, location ) " \
        + "VALUES ( %s, %s, %s, %s, %s) " \
        + "RETURNING id;"
    args = ( gene_name, annotation_key, 
             '(\"%s\", %i)' % (gene.chrm, assembly_id),
             gene.strand, 
             '[%s, %s]' % (gene.start, gene.stop) )
    cursor.execute( query, args )
    gene_id = int(cursor.fetchone()[0])
    cursor.close()
    
    for trans in gene.transcripts:
        add_transcript_to_db( gene_id, (gene.chrm, assembly_id), trans, conn )

def add_annotation_to_db( conn, name, description='NULL' ):
    cursor = conn.cursor()
    cursor.execute("INSERT INTO annotations.annotations ( name, description )"
                   + "VALUES ( %s, %s ) RETURNING id", (name, description) )
    rv = cursor.fetchone()[0]
    cursor.close()
    return int(rv)

def parse_arguments():
    import argparse

    parser = argparse.ArgumentParser(description='Load GTF file into database.')
    parser.add_argument( 'gtf', type=file, help='GTF file to load.')

    # TODO add code to determine this automatically
    parser.add_argument( '--annotation-name', required=True, 
           help='Name of the annotation we are inserting. ' )
    parser.add_argument( '--assembly-id', required=True, 
           help='ID of the assembly this annotation is based upon.' )

    parser.add_argument( '--db-name', default='rnaseq', 
                         help='Database to insert the data into. ' )
    parser.add_argument( '--db-host', 
                         help='Database host. default: socket connection' )
    parser.add_argument( '--db-user', 
                         help='Database connection user. Default: unix user' )
    parser.add_argument( '--db-pass', help='DB connection password.' )
    
    parser.add_argument( '--verbose', '-v', default=False, action='store_true',\
                             help='Whether or not to print status information.')
    args = parser.parse_args()
    
    global VERBOSE
    VERBOSE = args.verbose

    conn_str = "dbname=%s" % args.db_name
    conn = psycopg2.connect(conn_str)

    return args.gtf, args.annotation_name, int(args.assembly_id), conn

def main():
    gtf_fp, ann_name, assembly_id, conn = parse_arguments()
    
    # add the annotation, and return the pkey
    annotation_key = add_annotation_to_db( conn, ann_name )
    
    # load the genes
    genes = load_gtf( gtf_fp.name )
    
    # add all of the genes to the DB
    for i, gene in enumerate(genes):
        if VERBOSE: print "(%i/%i)    Processing %s" % ( i+1, len(genes), gene.id )
        add_gene_to_db( gene, annotation_key, assembly_id, conn )
    
    conn.commit()
    conn.close()

if __name__ == '__main__':
    main()