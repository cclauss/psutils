import pkg_resources

VERSION = pkg_resources.require('psutils')[0].version
version_banner=f'''\
%(prog)s {VERSION}
Copyright (c) Reuben Thomas 2023.
Released under the GPL version 3, or (at your option) any later version.
'''

import argparse
import re
import shutil
import sys
import warnings
from typing import List, NoReturn, Optional

from psutils import (
    HelpFormatter, comment, die, parsedraw, parse_file,
    parsepaper, setup_input_and_output, singledimen, simple_warning,
)

# Globals
flipping = False # any spec includes page flip
modulo = 1
scale = 1.0 # global scale factor
rotate = 0 # global rotation

# Command-line parsing helper functions
def specerror() -> NoReturn:
    die('''bad page specification:

  PAGESPECS = [MODULO:]SPEC
  SPEC      = [-]PAGENO[@SCALE][L|R|U|H|V][(XOFF,YOFF)][,SPEC|+SPEC]
              MODULO >= 1; 0 <= PAGENO < MODULO''')

class PageSpec:
    reversed: bool = False
    pageno: int = 0
    rotate: int = 0
    hflip: bool = False
    vflip: bool = False
    scale: float = 1.0
    xoff: float = 0.0
    yoff: float = 0.0

def parsespecs(s: str, width: Optional[float], height: Optional[float]) -> List[List[PageSpec]]:
    global modulo, flipping
    m = re.match(r'(?:([^:]+):)?(.*)', s)
    if not m:
        specerror()
    modulo, specs_text = int(m[1] or '1'), m[2]
    # Split on commas but not inside parentheses.
    pages_text = re.split(r',(?![^()]*\))', specs_text)
    pages = []
    angle = {'l': 90, 'r': -90, 'u': 180}
    for page in pages_text:
        specs = []
        specs_text = page.split('+')
        for spec_text in specs_text:
            m = re.match(r'(-)?(\d+)([LRUHV]+)?(?:@([^()]+))?(?:\((-?[\d.a-z]+),(-?[\d.a-z]+)\))?$', spec_text, re.IGNORECASE | re.ASCII)
            if not m:
                specerror()
            spec = PageSpec()
            if m[1] is not None:
                spec.reversed = True
            if m[2] is not None:
                spec.pageno = int(m[2])
            if m[4] is not None:
                spec.scale = float(m[4])
            if m[5] is not None:
                spec.xoff = singledimen(m[5], width, height)
            if m[6] is not None:
                spec.yoff = singledimen(m[6], width, height)
            if spec.pageno >= modulo:
                specerror()
            if m[3] is not None:
                for mod in m[3]:
                    if re.match(r'[LRU]', mod, re.IGNORECASE):
                        spec.rotate += angle[mod.lower()]
                    elif re.match(r'H', mod, re.IGNORECASE):
                        spec.hflip = not spec.hflip
                    elif re.match(r'V', mod, re.IGNORECASE):
                        spec.vflip = not spec.vflip
            # Normalize rotation and flips
            if spec.hflip == spec.vflip == 1:
                spec.hflip, spec.vflip = False, False
                spec.rotate += 180
            spec.rotate %= 360
            if spec.hflip or spec.vflip:
                flipping = True
            specs.append(spec)
        pages.append(specs)
    return pages

class Range:
    start: int
    end: int
    text: str

def parserange(ranges_text: str) -> List[Range]:
    ranges = []
    for range_text in ranges_text.split(','):
        r = Range()
        if range_text == '_':
            r.start, r.end = 0, 0 # so page_to_real_page() returns -1
        else:
            m = re.match(r'(_?\d+)?(?:(-)(_?\d+))?$', range_text)
            if not m:
                die(f"`{range_text}' is not a page range")
            start = m[1] or '1'
            end = (m[3] or '-1') if m[2] else m[1]
            start = re.sub('^_', '-', start)
            end = re.sub('^_', '-', end)
            r.start, r.end = int(start), int(end)
        r.text = range_text
        ranges.append(r)
    return ranges

def get_parser() -> argparse.ArgumentParser:
    # Command-line arguments
    parser = argparse.ArgumentParser(
        description='Rearrange pages of a PostScript document.',
        formatter_class=HelpFormatter,
        usage='%(prog)s [OPTION...] [INFILE [OUTFILE]]',
        add_help=False,
        epilog='''
PAGES is a comma-separated list of pages and page ranges.

SPECS is a list of page specifications [default is "0", which selects
each page in its normal order].
''',
    )
    warnings.showwarning = simple_warning(parser.prog)

    # Command-line parser
    parser.add_argument('-S', '--specs', default='0',
                        help='page specifications (see below)')
    parser.add_argument('-R', '--pages', dest='pagerange', type=parserange,
                        help='select the given page ranges')
    parser.add_argument('-e', '--even', action='store_true',
                        help='select even-numbered output pages')
    parser.add_argument('-o', '--odd', action='store_true',
                        help='select odd-numbered output pages')
    parser.add_argument('-r', '--reverse', action='store_true',
                        help='reverse the order of the output pages')
    parser.add_argument('-p', '--paper', type=parsepaper,
                        help='output paper name or dimensions (WIDTHxHEIGHT)')
    parser.add_argument('-P', '--inpaper', type=parsepaper,
                        help='input paper name or dimensions (WIDTHxHEIGHT)')
    parser.add_argument('-d', '--draw', metavar='DIMENSION', nargs='?',
                        type=parsedraw, default=0,
                        help='''\
draw a line of given width (relative to original
page) around each page [argument defaults to 1pt;
default is no line]''')
    parser.add_argument('-b', '--nobind', dest='nobinding', action='store_true',
                        help='''\
disable PostScript bind operators in prolog;
may be needed for complex page rearrangements''')
    parser.add_argument('-q', '--quiet', action='store_false', dest='verbose',
                        help="don't show page numbers being output")
    parser.add_argument('--help', action='help',
                        help='show this help message and exit')
    parser.add_argument('-v', '--version', action='version',
                        version=version_banner)
    parser.add_argument('infile', metavar='INFILE', nargs='?',
                        help="`-' or no INFILE argument means standard input")
    parser.add_argument('outfile', metavar='OUTFILE', nargs='?',
                        help="`-' or no OUTFILE argument means standard output")

    return parser

# PStoPS procset
# Wrap showpage, erasepage and copypage in our own versions.
# Nullify paper size operators.
procset = '''userdict begin
[/showpage/erasepage/copypage]{dup where{pop dup load
 type/operatortype eq{ /PStoPSenablepage cvx 1 index
 load 1 array astore cvx {} bind /ifelse cvx 4 array
 astore cvx def}{pop}ifelse}{pop}ifelse}forall
 /PStoPSenablepage true def
[/letter/legal/executivepage/a4/a4small/b5/com10envelope
 /monarchenvelope/c5envelope/dlenvelope/lettersmall/note
 /folio/quarto/a5]{dup where{dup wcheck{exch{}put}
 {pop{}def}ifelse}{pop}ifelse}forall
/setpagedevice {pop}bind 1 index where{dup wcheck{3 1 roll put}
 {pop def}ifelse}{def}ifelse
/PStoPSmatrix matrix currentmatrix def
/PStoPSxform matrix def/PStoPSclip{clippath}def
/defaultmatrix{PStoPSmatrix exch PStoPSxform exch concatmatrix}bind def
/initmatrix{matrix defaultmatrix setmatrix}bind def
/initclip[{matrix currentmatrix PStoPSmatrix setmatrix
 [{currentpoint}stopped{$error/newerror false put{newpath}}
 {/newpath cvx 3 1 roll/moveto cvx 4 array astore cvx}ifelse]
 {[/newpath cvx{/moveto cvx}{/lineto cvx}
 {/curveto cvx}{/closepath cvx}pathforall]cvx exch pop}
 stopped{$error/errorname get/invalidaccess eq{cleartomark
 $error/newerror false put cvx exec}{stop}ifelse}if}bind aload pop
 /initclip dup load dup type dup/operatortype eq{pop exch pop}
 {dup/arraytype eq exch/packedarraytype eq or
  {dup xcheck{exch pop aload pop}{pop cvx}ifelse}
  {pop cvx}ifelse}ifelse
 {newpath PStoPSclip clip newpath exec setmatrix} bind aload pop]cvx def
/initgraphics{initmatrix newpath initclip 1 setlinewidth
 0 setlinecap 0 setlinejoin []0 setdash 0 setgray
 10 setmiterlimit}bind def
end\n'''

def main(argv: List[str]=sys.argv[1:]) -> None: # pylint: disable=dangerous-default-value
    global modulo

    args = get_parser().parse_intermixed_args(argv)
    width: Optional[float] = None
    height: Optional[float] = None
    iwidth: Optional[float] = None
    iheight: Optional[float] = None
    if args.paper:
        width, height = args.paper
    if args.inpaper:
        iwidth, iheight = args.inpaper
    specs = parsespecs(args.specs, width, height)

    if (width is None) ^ (height is None):
        die('output page width and height must both be set, or neither')
    if (iwidth is None) ^ (iheight is None):
        die('input page width and height must both be set, or neither')

    infile, outfile = setup_input_and_output(args.infile, args.outfile, True)
    if iwidth is None and width is not None:
        iwidth, iheight = width, height

    if iwidth is None and flipping:
        die('input page size must be set when flipping the page')

    # Parse input
    psinfo = parse_file(infile, width is not None)

    # Copy input file from current position up to new position to output file,
    # ignoring the lines starting at something ignorelist points to.
    # Updates ignorelist.
    def fcopy(upto: int, ignorelist: List[int]) -> None:
        here = infile.tell()
        while len(ignorelist) > 0 and ignorelist[0] < upto:
            while len(ignorelist) > 0 and ignorelist[0] < here:
                ignorelist.pop(0)
            if len(ignorelist) > 0:
                fcopy(ignorelist[0], [])
            try:
                infile.readline()
            except IOError:
                die('I/O error', 2)
            ignorelist.pop(0)
            here = infile.tell()

        try:
            outfile.write(infile.read(upto - here))
        except IOError:
            die('I/O error', 2)

    # Page spec routines for page rearrangement
    def abs_page(n: int) -> int:
        if n < 0:
            n += psinfo.pages + 1
            n = max(n, 1)
        return n

    def page_index_to_page_number(ps: PageSpec, maxpage: int, modulo: int, pagebase: int) -> int:
        return (maxpage - pagebase - modulo if ps.reversed else pagebase) + ps.pageno

    def ps_transform(ps: PageSpec) -> bool:
        return ps.rotate != 0 or ps.hflip or ps.vflip or ps.scale != 1.0 or ps.xoff != 0.0 or ps.yoff != 0.0

    def pstops(pagerange: List[Range], modulo: int, odd: bool, even: bool, reverse: bool, nobind: bool, specs: List[List[PageSpec]], draw: bool, ignorelist: List[int]) -> None:
        outputpage = 0
        # If no page range given, select all pages
        if pagerange is None:
            pagerange = parserange('1-_1')

        # Normalize end-relative pageranges
        for r in pagerange:
            r.start = abs_page(r.start)
            r.end = abs_page(r.end)

        # Get list of pages
        page_list: List[int] = []
        # Returns -1 for an inserted blank page (page number '_')
        def page_to_real_page(p: int) -> int:
            try:
                return page_list[p]
            except IndexError:
                return 0

        for r in pagerange:
            inc = -1 if r.end < r.start else 1
            currentpg = r.start
            while r.end - currentpg != -inc:
                if currentpg > psinfo.pages:
                    die(f"page range {r.text} is invalid", 2)
                if not(odd and (not even) and currentpg % 2 == 0) and not(even and not odd and currentpg % 2 == 1):
                    page_list.append(currentpg - 1)
                currentpg += inc
        pages_to_output = len(page_list)

        # Calculate highest page number output (including any blanks)
        maxpage = pages_to_output + (modulo - pages_to_output % modulo) % modulo

        # Reverse page list if reversing pages
        if reverse:
            page_list.reverse()

        # Work out whether we need procset
        global_transform = scale != 1.0 or rotate != 0
        use_procset = global_transform or any(len(page) > 1 or ps_transform(page[0]) for page in specs)

        # Rearrange pages
        # FIXME: doesn't cope properly with loaded definitions
        infile.seek(0)
        if psinfo.pagescmt:
            fcopy(psinfo.pagescmt, ignorelist)
            try:
                line = infile.readline()
            except IOError:
                die('I/O error in header', 2)
            if width is not None and height is not None:
                print(f'%%DocumentMedia: plain {int(width)} {int(height)} 0 () ()', file=outfile)
                print(f'%%BoundingBox: 0 0 {int(width)} {int(height)}', file=outfile)
            pagesperspec = len(specs)
            print(f'%%Pages: {int(maxpage / modulo) * pagesperspec} 0', file=outfile)
        fcopy(psinfo.headerpos, ignorelist)
        if use_procset: # Redefining '/bind' is a desperation measure!
            outfile.write(f'%%BeginProcSet: PStoPS{"-nobind" if nobind else ""} 1 15\n{procset}')
            if nobind:
                print('/bind{}def', file=outfile)
            print("%%EndProcSet", file=outfile)

        # Write prologue to end of setup section, skipping our procset if present
        # and we're outputting it (this allows us to upgrade our procset)
        if psinfo.endprocset and use_procset:
            fcopy(psinfo.beginprocset, [])
            infile.seek(psinfo.endprocset)
        fcopy(psinfo.endsetup, [])

        # Save transformation from original to current matrix
        if not psinfo.beginprocset and use_procset:
            print('''userdict/PStoPSxform PStoPSmatrix matrix currentmatrix
 matrix invertmatrix matrix concatmatrix
 matrix invertmatrix put''', file=outfile)

        # Write from end of setup to start of pages
        fcopy(psinfo.pageptr[0], [])

        pagebase = 0
        while pagebase < maxpage:
            for page in specs:
                spec_page_number = 0
                for ps in page:
                    page_number = page_index_to_page_number(ps, maxpage, modulo, pagebase)
                    real_page = page_to_real_page(page_number)
                    if page_number < pages_to_output and 0 <= real_page < psinfo.pages:
                        # Seek the page
                        p = real_page
                        infile.seek(psinfo.pageptr[p])
                        try:
                            line = infile.readline()
                            assert comment(line)[0] == 'Page:'
                        except IOError:
                            die(f'I/O error seeking page {p}', 2)
                    if spec_page_number == 0: # We are on a new output page
                        # Construct the page label from the input page numbers
                        pagelabels = []
                        for spec in page:
                            n = page_to_real_page(page_index_to_page_number(spec, maxpage, modulo, pagebase))
                            pagelabels.append(str(n + 1) if n >= 0 else '*')
                        pagelabel = ",".join(pagelabels)
                        # Write page comment
                        outputpage += 1
                        print(f'%%Page: ({pagelabel}) {outputpage}', file=outfile)
                        if args.verbose:
                            sys.stderr.write(f'[{pagelabel}] ')
                    if use_procset:
                        print('userdict/PStoPSsaved save put', file=outfile)
                    if global_transform or ps_transform(ps):
                        print('PStoPSmatrix setmatrix', file=outfile)
                        if ps.xoff is not None:
                            print(f"{ps.xoff:f} {ps.yoff:f} translate", file=outfile)
                        if ps.rotate != 0:
                            print(f"{(ps.rotate + rotate) % 360} rotate", file=outfile)
                        if ps.hflip == 1:
                            assert iwidth is not None
                            print(f"[ -1 0 0 1 {iwidth * ps.scale * scale:g} 0 ] concat", file=outfile)
                        if ps.vflip == 1:
                            assert iheight is not None
                            print(f"[ 1 0 0 -1 0 {iheight * ps.scale * scale:g} ] concat", file=outfile)
                        if ps.scale != 1.0:
                            print(f"{ps.scale * scale:f} dup scale", file=outfile)
                        print('userdict/PStoPSmatrix matrix currentmatrix put', file=outfile)
                        if iwidth is not None:
                            # pylint: disable=invalid-unary-operand-type
                            print(f'''userdict/PStoPSclip{{0 0 moveto
 {iwidth:f} 0 rlineto 0 {iheight:f} rlineto {-iwidth:f} 0 rlineto
 closepath}}put initclip''', file=outfile)
                            if draw > 0:
                                print(f'gsave clippath 0 setgray {draw} setlinewidth stroke grestore', file=outfile)
                    if spec_page_number < len(page) - 1:
                        print('/PStoPSenablepage false def', file=outfile)
                    if psinfo.beginprocset and page_number < pages_to_output and real_page < psinfo.pages:
                        # Search for page setup
                        while True:
                            try:
                                line = infile.readline()
                            except IOError:
                                die(f'I/O error reading page setup {outputpage}', 2)
                            if line.startswith('PStoPSxform'):
                                break
                            try:
                                print(line, file=outfile)
                            except IOError:
                                die(f'I/O error writing page setup {outputpage}', 2)
                    if not psinfo.beginprocset and use_procset:
                        print('PStoPSxform concat' , file=outfile)
                    if page_number < pages_to_output and 0 <= real_page < psinfo.pages:
                        # Write the body of a page
                        fcopy(psinfo.pageptr[real_page + 1], [])
                    else:
                        print('showpage', file=outfile)
                    if use_procset:
                        print('PStoPSsaved restore', file=outfile)
                    spec_page_number += 1

            pagebase += modulo

        # Write trailer
        # pylint: disable=invalid-sequence-index
        infile.seek(psinfo.pageptr[psinfo.pages])
        shutil.copyfileobj(infile, outfile)
        if args.verbose:
            print(f'\nWrote {outputpage} pages', file=sys.stderr)

    # Output the pages
    pstops(args.pagerange, modulo, args.odd, args.even, args.reverse, args.nobinding, specs, args.draw, psinfo.sizeheaders)


if __name__ == '__main__':
    main()
