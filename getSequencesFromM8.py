#! /usr/bin/python
"""
"""

from Bio import SeqIO, SeqRecord
from optparse import OptionParser
from edl.util import addUniversalOptions, setupLogging
from edl.blastm8 import addHitTableOptions, FilterParams, filterM8Stream, M8Stream, GFF
import sys, logging

def main():
    usage = "usage: %prog [OPTIONS] HIT_TABLE"
    description = """
    Parses a hit table and prints out a fasta database of the hits. Source fasta data must be supplied via STDIN or the -i flag.
    """

# command line options
    parser = OptionParser(usage, description=description, conflict_handler='resolve')
    parser.add_option("-o", "--outfile", dest="outfile",
                      metavar="OUTFILE", help="Write masked fasta output to OUTFILE (default is STDOUT).")
    parser.add_option("-i", "--infile", dest="fasta",
        metavar="FILE", help=" File containing the fasta (defaults to STDIN)")
    parser.add_option("-M", "--mask", dest="keep", default=True, 
            action="store_false", 
            help="Return unmatched sequence fragments instead of hits.")
    parser.add_option("-m", "--minLength", dest="minLength",type="int",
                      metavar="BASES", default=1,
                      help="minimum number of bases for sequences in output")
    parser.add_option("-n", "--numbering_prefix", default=None, help="If given, name extracted sequence with this scring followed by a sinmple counting index of all extracted sequences. For example, -n "r" would add _r1 to the end of the first extracted sequence and _r2 to the second, and so on. By default, extracted sequences are named with start_end positions.") 

    parser.add_option("-t", "--translate", default=False, action='store_true',
                     help="Transalte to Amino Acid sequences")

    addUniversalOptions(parser)
    addHitTableOptions(parser,flags='all')

    (options, args) = parser.parse_args()

    setupLogging(options, description)

    # check that we have blast file as argument
    if len(args) != 1:
        options.error("Please supply the name of a hit table as the only argument")
    blastFile=args[0]

    # set up input/output streams
    if options.fasta is None:
        fastaHandle = sys.stdin
        fastaStr='STDIN'
    else:
        fastaHandle = open(options.fasta, "rU")
        fastaStr=options.fasta
    logging.info("Extrating sequence fragments from %s based on hits in %s" % (fastaStr,blastFile))

    if options.outfile is None:
        logging.info("Writing %s sequences to STDOUT" % ('fasta'))
        outputHandle = sys.stdout
    else:
        logging.info("Writing %s sequences to %s" % ('fasta', options.outfile))
        outputHandle = open(options.outfile,'w')

    # load hit regions
    if options.keep:
        minHitLength=options.minLength
    else:
        minHitLength=1
    readHits = loadHitRegions(blastFile, minHitLength, options)
    logging.info("Found hits for %d reads" % (len(readHits)))

    # process the fasta file with hits
    extractHits(fastaHandle, outputHandle, readHits, options.translate, options.minLength, options.keep, options.numbering_prefix)

#############
# Functions #
#############
def die( msg ):
    sys.stderr.write( "%s\n" % msg )
    sys.exit()

def warn(msg):
    sys.stderr.write("WARNING: %s\n" % (msg))

def loadHitRegions(blastFile, minLength, options):
    """
    Parse a hit table into a map from read names to lists of (start,end,annot)
    """
    hitMap={}
    params=FilterParams.createFromOptions(options)
    m8stream=M8Stream(blastFile)
    hitcount=0
    readcount=0
    keepcount=0
    for (read, hits) in filterM8Stream(m8stream, params, returnLines=False):
        readcount+=1
        hitTuples=[]
        for hit in hits:
            hitcount+=1
            if abs(hit.qstart-hit.qend)+1<minLength:
                continue

            keepcount+=1
            if hit.format == GFF:
                annot="# %d # %d # %s # %s;evalue=%s" % (hit.qstart, hit.qend, hit.strand, hit.hitDesc, hit.evalue)
            elif hit.pctid is not None:
                annot="%s [%d,%d] %0.1f%% %d bits" % (hit.hit, hit.hstart, hit.hend, hit.pctid, hit.score)
            else:
                annot="%s [%d,%d] score: %d" % (hit.hit, hit.hstart, hit.hend, hit.score)

            if hit.format == GFF:
                reverse = hit.strand != "+"
            else:
                reverse = hit.hstart>hit.hend

            if reverse:
                # reverse if hit is backwards
                hitTuples.append((hit.qend, hit.qstart, annot))
            else:
                hitTuples.append((hit.qstart, hit.qend, annot))
        hitMap[read]=hitTuples

    logging.debug("Kept %d of %d hits to %d reads" % (keepcount, hitcount, readcount))
    return hitMap

def extractHits(fasta, output, hits, translate, minLength, keep, numbering_prefix):
    """
    read sequences from a fasta stream
    look up regions to extract from 'hits' dict
    print to output as fasta (if over minimum):
        record fragments inside hits
    """
    recordsIn=0
    recordsOut=0
    recordsHit=0
    recordsDropped=0
    for record in SeqIO.parse(fasta, "fasta"):
        recordsIn+=1
        try:
            hitSpans = hits[record.id]
        except KeyError:
            recordsDropped+=1
            continue

        recordsHit+=1
        if keep:
            records=extractRecords(record,hitSpans,translate,numbering_prefix)
        else:
            records=maskRead(record,hitSpans,minLength)

        if len(records)>0:
            recordsOut+=SeqIO.write(records,output, "fasta")

    logging.info("%d of %d records hit.\n%d records written" % (recordsHit,
                                                          recordsIn,
                                                          recordsOut))
    logging.debug("%d records dropped (no hit)" % (recordsDropped))

def extractRecords(record, hitSpans, translate, numbering_prefix):
    """
    given a seqeunce record and a list of spans of the mask:
        return a list records of the matched sequence fragments
         - one for each hit
    """
    records=[]
    for span in hitSpans:
        (start,end)=span[0:2]
        size = end-start+1

        # build SeqRecord object
        if start>end:
            newRec=record[end-1:start]
            newRec.seq=newRec.seq.reverse_complement()
        else:
            newRec=record[start-1:end]
        if translate:
            newRec.seq=newRec.seq.translate()

        # annotations
        if numbering_prefix:
            name_suff = "%s%d" % (numbering_prefix, len(records))
        else:
            name_suff = "_%d_%d" % (start, end)
        newRec.id=newRec.id+name_suff

        if len(span)==3:
            newRec.description=(span[2])
        else:
            newRec.description=("%d bases from %s" % (size,newRec.name))

        records.append(newRec)

    return records

def maskRead(record, hitSpans, minLength):
    """
    given a seqeunce record, a list of spans of the mask,
     and a smallest fragment size:
    return a list of up new record objects consisting of
     all the unmatched space between hits (where the space is longer than the min length)
    """
    if minLength<1:
        minLength=1
    logging.debug("Hits: %r" % (hitSpans))
    newSpans=[[1,len(record)],]
    # Start with the full span and chip away hits to reveal unhit spans
    for i in reversed(range(len(hitSpans))):
        (start,end)=hitSpans[i][0:2]
        if start>end:
            stemp=start
            start=end
            end=stemp

        for j in reversed(range(len(newSpans))):
            (ustart,uend)=newSpans[j][0:2]
            if (ustart>end) or (uend<start):
                # hit does not overlap this unmatched region
                continue
            if start<ustart+minLength:
                if end>=uend-minLength:
                    # Complete overlap, drop unhit span (it's been hit!)
                    newSpans.pop(j)
                else:
                    # PArtial overlap at start, clip the start
                    newSpans[j][0]=end+1
            else:
                # Clip the end
                newSpans[j][1]=start-1
                if end<uend-minLength:
                    # Add new unhit span if overlap on both ends
                    newSpans.append([end+1,uend])

    records=[]
    for span in newSpans:
        (start,end)=span[0:2]
        size = end-start+1

        # build SeqRecord object
        if start>end:
            newRec=record[end-1:start]
            newRec.seq=newRec.seq.reverse_complement()
        else:
            newRec=record[start-1:end]

        # annotations
        newRec.id=newRec.id+("_%d_%d"%(start,end))
        newRec.description=("%d bases from %s" % (size,newRec.name))

        records.append(newRec)

    return records

if __name__ == '__main__':
    main()
