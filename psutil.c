/* psutil.c
 * Copyright (C) Angus J. C. Duggan 1991-1995
 * See file LICENSE for details.
 *
 * utilities for PS programs
 */


/*
 *  Daniele Giacomini appunti2@gmail.com 2010-09-02
 *    Changed to using ftello() and fseeko()
 *  AJCD 6/4/93
 *    Changed to using ftell() and fseek() only (no length calculations)
 *  Hunter Goatley    31-MAY-1993 23:33
 *    Fixed VMS support.
 *  Hunter Goatley     2-MAR-1993 14:41
 *    Added VMS support.
 */

#define _FILE_OFFSET_BITS 64

#include "psutil.h"
#include "pserror.h"
#include "psspec.h"
#include "patchlev.h"

#include <string.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <stdio.h>

#include <paper.h>

#define iscomment(x,y) (strncmp(x,y,strlen(y)) == 0)

extern char *program ;
extern int pages;
extern int verbose;
extern FILE *infile;
extern FILE *outfile;
extern char pagelabel[BUFSIZ];
extern int pageno;

static char buffer[BUFSIZ];
static long bytes = 0;
static off_t pagescmt = 0;
static off_t headerpos = 0;
static off_t endsetup = 0;
static off_t beginprocset = 0;		/* start of pstops procset */
static off_t endprocset = 0;
static int outputpage = 0;
static int maxpages = 100;
static off_t *pageptr;

static void maybe_init_libpaper(void)
{
  static int libpaper_initted = 0;
  if (!libpaper_initted) {
    paperinit();
    libpaper_initted = 1;
  }
}

void set_paper_size(const char *paper_name)
{
  const struct paper *paper = get_paper(paper_name);
  if (paper) {
    width = paperpswidth(paper);
    height = paperpsheight(paper);
  }
}

const struct paper *get_paper(const char *paper_name)
{
  const char *default_paper;
  maybe_init_libpaper();
  default_paper = systempapername();
  return paperinfo(default_paper);
}

/* Make a file seekable, using temporary files if necessary */
FILE *seekable(FILE *fp)
{
#ifndef MSDOS
  FILE *ft;
  long r, w ;
#endif
  char *p;
  char buffer[BUFSIZ] ;
#if defined(WINNT)
  struct _stat fs ;
#else
  off_t fpos;
#endif

#if defined(WINNT)
  if (_fstat(fileno(fp), &fs) == 0 && (fs.st_mode&_S_IFREG) != 0)
    return (fp);
#else
  if ((fpos = ftello(fp)) >= 0)
    if (!fseeko(fp, (off_t) 0, SEEK_END) && !fseeko(fp, fpos, SEEK_SET))
      return (fp);
#endif

#if defined(MSDOS)
  message(FATAL, "input is not seekable\n");
  return (NULL) ;
#else
  if ((ft = tmpfile()) == NULL)
    return (NULL);

  while ((r = fread(p = buffer, sizeof(char), BUFSIZ, fp)) > 0) {
    do {
      if ((w = fwrite(p, sizeof(char), r, ft)) == 0)
	return (NULL) ;
      p += w ;
      r -= w ;
    } while (r > 0) ;
  }

  if (!feof(fp))
    return (NULL) ;

  /* discard the input file, and rewind the temporary */
  (void) fclose(fp);
  if (fseeko(ft, (off_t) 0, SEEK_SET) != 0)
    return (NULL) ;

  return (ft);
#endif
}


/* copy input file from current position upto new position to output file,
 * ignoring the lines starting at something ignorelist points to */
static int fcopy(off_t upto, off_t *ignorelist)
{
  off_t here = ftello(infile);
  off_t bytes_left;

  if (ignorelist != NULL) {
    while (*ignorelist > 0 && *ignorelist < here)
      ignorelist++;

    while (*ignorelist > 0 && *ignorelist < upto) {
      int r = fcopy(*ignorelist, NULL);
      if (!r || fgets(buffer, BUFSIZ, infile) == NULL)
	return 0;
      ignorelist++;
      here = ftello(infile);
      while (*ignorelist > 0 && *ignorelist < here)
	ignorelist++;
    }
  }
  bytes_left = upto - here;

  while (bytes_left > 0) {
    size_t rw_result;
    const size_t numtocopy = (bytes_left > BUFSIZ) ? BUFSIZ : bytes_left;
    rw_result = fread(buffer, 1, numtocopy, infile);
    if (rw_result < numtocopy) return (0);
    rw_result = fwrite(buffer, 1, numtocopy, outfile);
    if (rw_result < numtocopy) return (0);
    bytes_left -= numtocopy;
    bytes += numtocopy;
  }
  return (1);
}

/* build array of pointers to start/end of pages */
void scanpages(off_t *sizeheaders)
{
   register char *comment = buffer+2;
   register int nesting = 0;
   register off_t record;

   if (sizeheaders)
     *sizeheaders = 0;

   if ((pageptr = (off_t *)malloc(sizeof(off_t)*maxpages)) == NULL)
      message(FATAL, "out of memory\n");
   pages = 0;
   fseeko(infile, (off_t) 0, SEEK_SET);
   while (record = ftello(infile), fgets(buffer, BUFSIZ, infile) != NULL)
      if (*buffer == '%') {
	 if (buffer[1] == '%') {
	    if (nesting == 0 && iscomment(comment, "Page:")) {
	       if (pages >= maxpages-1) {
		  maxpages *= 2;
		  if ((pageptr = (off_t *)realloc((char *)pageptr,
					     sizeof(off_t)*maxpages)) == NULL)
		     message(FATAL, "out of memory\n");
	       }
	       pageptr[pages++] = record;
	    } else if (headerpos == 0 && iscomment(comment, "BoundingBox:")) {
	       if (sizeheaders) {
		  *(sizeheaders++) = record;
		  *sizeheaders = 0;
	       }
	    } else if (headerpos == 0 && iscomment(comment, "HiResBoundingBox:")) {
	       if (sizeheaders) {
		  *(sizeheaders++) = record;
		  *sizeheaders = 0;
	       }
	    } else if (headerpos == 0 && iscomment(comment,"DocumentPaperSizes:")) {
	       if (sizeheaders) {
		  *(sizeheaders++) = record;
		  *sizeheaders = 0;
	       }
	    } else if (headerpos == 0 && iscomment(comment,"DocumentMedia:")) {
	       if (sizeheaders) {
		  *(sizeheaders++) = record;
		  *sizeheaders = 0;
	       }
	    } else if (headerpos == 0 && iscomment(comment, "Pages:"))
	       pagescmt = record;
	    else if (headerpos == 0 && iscomment(comment, "EndComments"))
	       headerpos = ftello(infile);
	    else if (iscomment(comment, "BeginDocument") ||
		     iscomment(comment, "BeginBinary") ||
		     iscomment(comment, "BeginFile"))
	       nesting++;
	    else if (iscomment(comment, "EndDocument") ||
		     iscomment(comment, "EndBinary") ||
		     iscomment(comment, "EndFile"))
	       nesting--;
	    else if (nesting == 0 && iscomment(comment, "EndSetup"))
	       endsetup = record;
	    else if (nesting == 0 && iscomment(comment, "BeginProlog"))
	       headerpos = ftello(infile);
	    else if (nesting == 0 &&
		       iscomment(comment, "BeginProcSet: PStoPS"))
	       beginprocset = record;
	    else if (beginprocset && !endprocset &&
		     iscomment(comment, "EndProcSet"))
	       endprocset = ftello(infile);
	    else if (nesting == 0 && (iscomment(comment, "Trailer") ||
				      iscomment(comment, "EOF"))) {
	       fseeko(infile, record, SEEK_SET);
	       break;
	    }
	 } else if (headerpos == 0 && buffer[1] != '!')
	    headerpos = record;
      } else if (headerpos == 0)
	 headerpos = record;
   pageptr[pages] = ftello(infile);
   if (endsetup == 0 || endsetup > pageptr[0])
      endsetup = pageptr[0];
}

/* seek a particular page */
void seekpage(int p)
{
   fseeko(infile, pageptr[p], SEEK_SET);
   if (fgets(buffer, BUFSIZ, infile) != NULL &&
       iscomment(buffer, "%%Page:")) {
      char *start, *end;
      for (start = buffer+7; isspace(*start); start++);
      if (*start == '(') {
	 int paren = 1;
	 for (end = start+1; paren > 0; end++)
	    switch (*end) {
	    case '\0':
	       message(FATAL, "Bad page label while seeking page %d\n", p);
	    case '(':
	       paren++;
	       break;
	    case ')':
	       paren--;
	       break;
	    }
      } else
	 for (end = start; !isspace(*end); end++);
      strncpy(pagelabel, start, end-start);
      pagelabel[end-start] = '\0';
      pageno = atoi(end);
   } else
      message(FATAL, "I/O error seeking page %d\n", p);
}

/* Output routines. These all update the global variable bytes with the number
 * of bytes written */
void writestring(char *s)
{
   fputs(s, outfile);
   bytes += strlen(s);
}

/* write page comment */
void writepageheader(char *label, int page)
{
   if (verbose)
      message(LOG, "[%d] ", page);
   sprintf(buffer, "%%%%Page: %s %d\n", label, ++outputpage);
   writestring(buffer);
}

/* search for page setup */
void writepagesetup(void)
{
   char buffer[BUFSIZ];
   if (beginprocset) {
      for (;;) {
	 if (fgets(buffer, BUFSIZ, infile) == NULL)
	    message(FATAL, "I/O error reading page setup %d\n", outputpage);
	 if (!strncmp(buffer, "PStoPSxform", 11))
	    break;
	 if (fputs(buffer, outfile) == EOF)
	    message(FATAL, "I/O error writing page setup %d\n", outputpage);
	 bytes += strlen(buffer);
      }
   }
}

/* write the body of a page */
void writepagebody(int p)
{
   if (!fcopy(pageptr[p+1], NULL))
      message(FATAL, "I/O error writing page %d\n", outputpage);
}

/* write a whole page */
void writepage(int p)
{
   seekpage(p);
   writepageheader(pagelabel, p+1);
   writepagebody(p);
}

/* write from start of file to end of header comments */
void writeheader(int p, off_t *ignore)
{
   writeheadermedia(p, ignore, -1, -1);
}

void writeheadermedia(int p, off_t *ignore, double width, double height)
{
    fseeko(infile, (off_t) 0, SEEK_SET);
   if (pagescmt) {
      if (!fcopy(pagescmt, ignore) || fgets(buffer, BUFSIZ, infile) == NULL)
	 message(FATAL, "I/O error in header\n");
      if (width > -1 && height > -1) {
         sprintf(buffer, "%%%%DocumentMedia: plain %d %d 0 () ()\n", (int) width, (int) height);
         writestring(buffer);
         sprintf(buffer, "%%%%BoundingBox: 0 0 %d %d\n", (int) width, (int) height);
         writestring(buffer);
      }
      sprintf(buffer, "%%%%Pages: %d 0\n", p);
      writestring(buffer);
   }
   if (!fcopy(headerpos, ignore))
      message(FATAL, "I/O error in header\n");
}

/* write prologue to end of setup section excluding PStoPS procset */
int writepartprolog(void)
{
   if (beginprocset && !fcopy(beginprocset, NULL))
      message(FATAL, "I/O error in prologue\n");
   if (endprocset)
      fseeko(infile, endprocset, SEEK_SET);
   writeprolog();
   return !beginprocset;
}

/* write prologue up to end of setup section */
void writeprolog(void)
{
   if (!fcopy(endsetup, NULL))
      message(FATAL, "I/O error in prologue\n");
}

/* write from end of setup to start of pages */
void writesetup(void)
{
   if (!fcopy(pageptr[0], NULL))
      message(FATAL, "I/O error in prologue\n");
}

/* write trailer */
void writetrailer(void)
{
   fseeko(infile, pageptr[pages], SEEK_SET);
   while (fgets(buffer, BUFSIZ, infile) != NULL) {
      writestring(buffer);
   }
   if (verbose)
      message(LOG, "Wrote %d pages, %ld bytes\n", outputpage, bytes);
}

/* write a page with nothing on it */
void writeemptypage(void)
{
   if (verbose)
      message(LOG, "[*] ");
   sprintf(buffer, "%%%%Page: * %d\n", ++outputpage);
   writestring(buffer);
   if (beginprocset)
      writestring("PStoPSxform concat\n");
   writestring("showpage\n");
}

