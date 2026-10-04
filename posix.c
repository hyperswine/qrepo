/* QRepo's host seam: descriptor-relative, no-follow filesystem access.
 * Repository format, hashing and command policy live in FP-RISC. */
#include "os_value.h"
#include <dirent.h>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <stdio.h>
#include <sys/random.h>
#include <signal.h>
static int rootfd = -1, lockfd = -1;
static unsigned long serial;
/* The metadata directory, by IDENTITY.  A name is not enough: on a
 * case-insensitive volume ".QREPO" opens .qrepo, and a file system may fold
 * Unicode forms or ignore characters besides.  So whatever a path's
 * components are called, none of them may BE this directory unless the path
 * is literally under ".qrepo".  (A tree from a remote that named
 * ".QREPO/checkout.json" used to write into the repository's own metadata.) */
static dev_t metadev;
static ino_t metaino;
static const char *refusal; /* why parent() refused, when errno does not say */
static int is_meta(int fd) { struct stat st; return !fstat(fd, &st) && st.st_dev == metadev && st.st_ino == metaino; }
static V failed(void) { if (refusal) { const char *why = refusal; refusal = NULL; return os_err(why); } return os_errno(); }
/* Fault injection exists only in a separately built test executable. */
static void fault(const char *stage, V path) {
#ifdef QREPO_TEST_FAULTS
  const char *f=getenv("QREPO_FAULT");
  str_t *p=(str_t*)path;
  size_t n=strlen(stage);
  if(f && !strncmp(f,stage,n) && f[n]==':' && strlen(f+n+1)==p->len && !memcmp(f+n+1,p->bytes,p->len)) _exit(86);
#else
  (void)stage;(void)path;
#endif
}
/* Metadata paths are UTF-8; file contents are arbitrary bytes. */
static int utf8(const unsigned char *s,size_t n){
  for(size_t i=0;i<n;){unsigned c=s[i++];if(c<128)continue;
    unsigned v,minimum;size_t k;
    if(c>=0xc2&&c<=0xdf){v=c&31;k=1;minimum=128;}
    else if(c>=0xe0&&c<=0xef){v=c&15;k=2;minimum=2048;}
    else if(c>=0xf0&&c<=0xf4){v=c&7;k=3;minimum=65536;}
    else return 0;
    if(k>n-i)return 0;
    while(k--){unsigned d=s[i++];if((d&0xc0)!=0x80)return 0;v=(v<<6)|(d&63);}
    if(v<minimum||v>0x10ffff||(v>=0xd800&&v<=0xdfff))return 0;
  }return 1;
}
static char *pathstr(V v) {
  char *s = os_cstr(v, "Qfs path must be a String");
  if (strlen(s) != ((str_t *)v)->len || !utf8((unsigned char*)s,strlen(s))) { free(s); errno = EINVAL; return NULL; }
  return s;
}
/* Open every ancestor without following symlinks. Caller owns fd and leaf. */
static int parent(V v, char **leaf) {
  char *s = pathstr(v); if (!s) return -1;
  if (!*s || *s == '/' || s[strlen(s)-1] == '/') { free(s); errno = EINVAL; return -1; }
  int fd = dup(rootfd); if (fd < 0) { free(s); return -1; }
  char *p = s;
  refusal = NULL;
  for (int depth = 0;; depth++) {
    char *slash = strchr(p, '/'); if (slash) *slash = 0;
    if (!*p || !strcmp(p,".") || !strcmp(p,"..")) { close(fd); free(s); errno=EINVAL; return -1; }
    if (!slash) {
      /* the leaf may be the metadata directory only by its own name */
      struct stat st;
      if (strcmp(p, ".qrepo") && !fstatat(fd, p, &st, AT_SYMLINK_NOFOLLOW) && st.st_dev == metadev && st.st_ino == metaino) {
        close(fd); free(s); errno = EPERM; refusal = "path names the repository metadata under another spelling"; return -1; }
      *leaf = strdup(p); free(s); if (!*leaf) { close(fd); errno=ENOMEM; return -1; } return fd; }
    int next = openat(fd,p,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    int e=errno; close(fd); if(next<0){free(s);errno=e;return -1;}
    if (is_meta(next) && (depth != 0 || strcmp(p, ".qrepo"))) {
      close(next); free(s); errno = EPERM; refusal = "path resolves into the repository metadata under another spelling"; return -1; }
    fd=next; p=slash+1;
  }
}
static V begin(V rv, V initv) {
  char *root=pathstr(rv); if(!root)return os_errno();
  rootfd=open(root,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);free(root);
  if(rootfd<0)return os_errno();
  if(UNTAG(initv) && mkdirat(rootfd,".qrepo",0700)<0 && errno!=EEXIST)return os_errno();
  int meta=openat(rootfd,".qrepo",O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
  if(meta<0)return os_errno();
  lockfd=openat(meta,"command.lock",O_RDWR|O_CREAT|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC,0600);
  if(lockfd<0){close(meta);return os_errno();}
  struct stat st;
  if(fstat(lockfd,&st)<0 || !S_ISREG(st.st_mode) || st.st_nlink!=1){close(meta);return os_err("invalid repository lock");}
  struct stat ms; if(fstat(meta,&ms)<0){close(meta);return os_errno();} metadev=ms.st_dev; metaino=ms.st_ino;
  if(flock(lockfd,LOCK_EX)<0){close(meta);return os_errno();}
  if(UNTAG(initv) && (fsync(meta)<0 || fsync(rootfd)<0)){close(meta);return os_errno();}
  close(meta);return os_ok((V)&fpr_unit);
}
FPR_FN_CSTACK(fpr_g_Qfs_x2ebegin,begin,2);
static V kind(V pv){
  char *name=NULL;int fd=parent(pv,&name);
  /* no such ancestor, or an ancestor that is a file: the path does not exist */
  if(fd<0)return (!refusal && (errno==ENOENT||errno==ENOTDIR))?os_ok(TAG(-1)):failed();
  struct stat st;int r=fstatat(fd,name,&st,AT_SYMLINK_NOFOLLOW);int e=errno;free(name);close(fd);
  if(r<0){if(e==ENOENT)return os_ok(TAG(-1));errno=e;return os_errno();}
  return os_ok(TAG(S_ISREG(st.st_mode)?0:S_ISDIR(st.st_mode)?1:2));
}
FPR_FN_CSTACK(fpr_g_Qfs_x2ekind,kind,1);
static V readfile(V pv){
  char *name=NULL;int dir=parent(pv,&name);if(dir<0)return failed();
  int fd=openat(dir,name,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC);int e=errno;free(name);close(dir);
  if(fd<0){errno=e;return os_errno();}
  struct stat before,after;if(fstat(fd,&before)<0 || !S_ISREG(before.st_mode)){close(fd);return os_err("not a regular file");}
  buf_t b={0};
  for(;;){if(!buf_room(&b,65536)){close(fd);free(b.p);return os_err("out of memory");}
    ssize_t n=read(fd,b.p+b.n,b.cap-b.n);if(n<0 && errno==EINTR)continue;
    if(n<0){V err=os_errno();close(fd);free(b.p);return err;}if(!n)break;b.n+=n;}
  int changed=fstat(fd,&after)<0 || before.st_size!=after.st_size;
#ifdef __APPLE__
  changed=changed || before.st_mtimespec.tv_sec!=after.st_mtimespec.tv_sec || before.st_mtimespec.tv_nsec!=after.st_mtimespec.tv_nsec || before.st_ctimespec.tv_sec!=after.st_ctimespec.tv_sec || before.st_ctimespec.tv_nsec!=after.st_ctimespec.tv_nsec;
#else
  changed=changed || before.st_mtim.tv_sec!=after.st_mtim.tv_sec || before.st_mtim.tv_nsec!=after.st_mtim.tv_nsec || before.st_ctim.tv_sec!=after.st_ctim.tv_sec || before.st_ctim.tv_nsec!=after.st_ctim.tv_nsec;
#endif
  close(fd);if(changed){free(b.p);return os_err("file changed while reading; retry");}
  V out=os_ok(os_str(b.p?b.p:"",b.n));free(b.p);return out;
}
FPR_FN_CSTACK(fpr_g_Qfs_x2eread,readfile,1);
static int cmp(const void*a,const void*b){return strcmp(*(char*const*)a,*(char*const*)b);}
static V list(V pv){
  int fd;
  if(((str_t*)pv)->len==0)fd=openat(rootfd,".",O_RDONLY|O_DIRECTORY|O_CLOEXEC);
  else{char*name=NULL;int dir=parent(pv,&name);if(dir<0)return failed();fd=openat(dir,name,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);int e=errno;free(name);close(dir);errno=e;}
  if(fd<0)return os_errno();DIR*d=fdopendir(fd);if(!d){close(fd);return os_errno();}
  char **names=NULL;size_t n=0,cap=0;int err=0;
  for(;;){errno=0;struct dirent*de=readdir(d);if(!de){err=errno;break;}if(!strcmp(de->d_name,".")||!strcmp(de->d_name,".."))continue;
    if(!utf8((unsigned char*)de->d_name,strlen(de->d_name))){err=EILSEQ;break;}
    if(n==cap){size_t nc=cap?cap*2:64;char**p=realloc(names,nc*sizeof *names);if(!p){err=ENOMEM;break;}names=p;cap=nc;}
    names[n]=strdup(de->d_name);if(!names[n]){err=ENOMEM;break;}n++;}
  closedir(d);if(n>1)qsort(names,n,sizeof *names,cmp);V out=(V)&os_nil;
  for(size_t i=n;i-->0;){if(!err)out=os_cons(os_str(names[i],strlen(names[i])),out);free(names[i]);}free(names);
  if(err){errno=err;return os_errno();}return os_ok(out);
}
FPR_FN_CSTACK(fpr_g_Qfs_x2elist,list,1);
static V makedir(V pv){char*name=NULL;int fd=parent(pv,&name);if(fd<0)return failed();
  int r=mkdirat(fd,name,0700);if(r<0&&errno==EEXIST){int d=openat(fd,name,O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);if(d>=0){close(d);r=0;}}
  if(!r)r=fsync(fd);V out=os_unit_or_errno(r);free(name);close(fd);return out;}
FPR_FN_CSTACK(fpr_g_Qfs_x2emkdir,makedir,1);
/* mode 1 creates an immutable object exclusively; mode 0 replaces a ref or a
 * working file, keeping its permission bits.  `fill` writes the content into
 * a temp in the destination directory; then fsync, publish, fsync the parent.
 * Never truncate the target.  A fill that fails (a source that changed, an
 * object that does not hash to its name) publishes nothing. */
typedef int (*fill_fn)(int fd, void *ctx);
static V publish(V pv, int mode, fill_fn fill, void *ctx){
  char*name=NULL;int dir=parent(pv,&name);if(dir<0)return failed();
  struct stat st;int sr=fstatat(dir,name,&st,AT_SYMLINK_NOFOLLOW);
  if(sr==0&&!S_ISREG(st.st_mode)){free(name);close(dir);return os_err("not a regular file");}
  if(sr<0&&errno!=ENOENT){V out=os_errno();free(name);close(dir);return out;}
  if(sr==0&&mode){free(name);close(dir);return os_err("object already exists");}
  char tmp[96];int fd;
  do{snprintf(tmp,sizeof tmp,".qr-tmp-%ld-%lu",(long)getpid(),++serial);fd=openat(dir,tmp,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);}while(fd<0&&errno==EEXIST);
  if(fd<0){V out=os_errno();free(name);close(dir);return out;}
  refusal=NULL;
  int err=fill(fd,ctx);
  if(!err&&sr==0&&fchmod(fd,st.st_mode&0777)<0)err=errno;
  if(!err&&fsync(fd)<0)err=errno;if(close(fd)<0&&!err)err=errno;
  if(!err)fault("before",pv);
  if(!err){int r=mode?linkat(dir,tmp,dir,name,0):renameat(dir,tmp,dir,name);if(r<0)err=errno;}
  if(!err)fault("after",pv);
  unlinkat(dir,tmp,0);
  if(!err&&fsync(dir)<0)err=errno;
  free(name);close(dir);if(err){errno=err;return failed();}return os_ok((V)&fpr_unit);
}
static int writeall(int fd,const void*p,size_t n){const char*b=p;size_t at=0;
  while(at<n){ssize_t k=write(fd,b+at,n-at);if(k<0&&errno==EINTR)continue;if(k<=0)return k<0?errno:EIO;at+=k;}return 0;}
static int fill_bytes(int fd,void*ctx){str_t*b=ctx;return writeall(fd,b->bytes,b->len);}
static V writefile(V pv,V bv,V mv){return publish(pv,(int)UNTAG(mv),fill_bytes,(void*)bv);}
FPR_FN_CSTACK(fpr_g_Qfs_x2ewrite,writefile,3);

/* Use the host's vetted SHA-256; never launch a hashing subprocess. */
#ifdef __APPLE__
#include <CommonCrypto/CommonDigest.h>
#else
#include <openssl/sha.h>
#endif
static V sha256(V bv){
  str_t*b=(str_t*)bv;unsigned char digest[32];
#ifdef __APPLE__
  CC_SHA256_CTX ctx;CC_SHA256_Init(&ctx);
  size_t pos=0;while(pos<b->len){size_t n=b->len-pos;if(n>1048576)n=1048576;CC_SHA256_Update(&ctx,b->bytes+pos,(CC_LONG)n);pos+=n;}
  CC_SHA256_Final(digest,&ctx);
#else
  SHA256(b->bytes,b->len,digest);
#endif
  char hex[64];const char*digits="0123456789abcdef";
  for(int i=0;i<32;i++){hex[2*i]=digits[digest[i]>>4];hex[2*i+1]=digits[digest[i]&15];}
  return os_str(hex,64);
}
FPR_FN_CSTACK(fpr_g_Qfs_x2esha256,sha256,1);
static V output(V bv){str_t*b=(str_t*)bv;size_t at=0;while(at<b->len){ssize_t n=write(STDOUT_FILENO,b->bytes+at,b->len-at);if(n<0&&errno==EINTR)continue;if(n<=0)return os_errno();at+=n;}return os_ok((V)&fpr_unit);}
FPR_FN_CSTACK(fpr_g_Qfs_x2eoutput,output,1);

/* ---- Streaming: a file's bytes never enter the FP-RISC heap -------------
 * The heap is reclaimed only at arena boundaries or exit, so a command that
 * read every file it touched held all of them at once.  These move bytes
 * between descriptors in 64 KiB pieces; what reaches FP-RISC is an identity,
 * a size, or a few bytes of a frame header.  A span is (offset, length);
 * length -1 means "to the end", and only then is the source required to be
 * unchanged by size and timestamps, as Qfs.read requires. */
#ifdef __APPLE__
typedef CC_SHA256_CTX hctx;
static int h_init(hctx*c){CC_SHA256_Init(c);return 1;}
static void h_update(hctx*c,const void*p,size_t n){CC_SHA256_Update(c,p,(CC_LONG)n);}
static void h_final(hctx*c,unsigned char*d){CC_SHA256_Final(d,c);}
#else
#include <openssl/evp.h>
typedef struct { EVP_MD_CTX *m; } hctx;
static int h_init(hctx*c){c->m=EVP_MD_CTX_new();return c->m&&EVP_DigestInit_ex(c->m,EVP_sha256(),NULL);}
static void h_update(hctx*c,const void*p,size_t n){EVP_DigestUpdate(c->m,p,n);}
static void h_final(hctx*c,unsigned char*d){unsigned int l;EVP_DigestFinal_ex(c->m,d,&l);EVP_MD_CTX_free(c->m);}
#endif
static void hexof(const unsigned char*d,char*hex){const char*digits="0123456789abcdef";
  for(int i=0;i<32;i++){hex[2*i]=digits[d[i]>>4];hex[2*i+1]=digits[d[i]&15];}}
static long long num(V v){if(!ISINT(v))fpr_cpanic("Qfs: expected an Int");return (long long)UNTAG(v);}

/* a regular file, opened without following links */
static int openreg(V pv,struct stat*st){
  char*name=NULL;int dir=parent(pv,&name);if(dir<0)return -1;
  int fd=openat(dir,name,O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC);int e=errno;free(name);close(dir);
  if(fd<0){errno=e;return -1;}
  if(fstat(fd,st)<0||!S_ISREG(st->st_mode)){close(fd);errno=EINVAL;refusal="not a regular file";return -1;}
  return fd;
}
static int same(int fd,const struct stat*before){struct stat after;
  if(fstat(fd,&after)<0||before->st_size!=after.st_size)return 0;
#ifdef __APPLE__
  return before->st_mtimespec.tv_sec==after.st_mtimespec.tv_sec&&before->st_mtimespec.tv_nsec==after.st_mtimespec.tv_nsec&&before->st_ctimespec.tv_sec==after.st_ctimespec.tv_sec&&before->st_ctimespec.tv_nsec==after.st_ctimespec.tv_nsec;
#else
  return before->st_mtim.tv_sec==after.st_mtim.tv_sec&&before->st_mtim.tv_nsec==after.st_mtim.tv_nsec&&before->st_ctim.tv_sec==after.st_ctim.tv_sec&&before->st_ctim.tv_nsec==after.st_ctim.tv_nsec;
#endif
}
/* Copy `len` bytes (-1: to the end) from `in` at its offset to `out` (-1:
 * nowhere), hashing them into `c` (NULL: not).  0, or an errno. */
static int pump(int in,long long len,int out,hctx*c){
  unsigned char*buf=malloc(65536);if(!buf)return ENOMEM;int err=0;
  while(len){size_t want=(len<0||len>65536)?65536:(size_t)len;
    ssize_t n=read(in,buf,want);if(n<0&&errno==EINTR)continue;
    if(n<0){err=errno;break;}
    if(n==0){if(len>0){refusal="the source is shorter than its span";err=EIO;}break;}
    if(c)h_update(c,buf,n);
    if(out>=0&&(err=writeall(out,buf,n)))break;
    if(len>0)len-=n;}
  free(buf);return err;
}
/* open a source span: seek to `off`, and say whether it runs to the end */
static int openspan(V pv,long long off,long long len,struct stat*st){
  if(off<0||len<-1){errno=EINVAL;return -1;}
  int fd=openreg(pv,st);if(fd<0)return -1;
  if(off+(len<0?0:len)>st->st_size||lseek(fd,off,SEEK_SET)<0){close(fd);errno=EINVAL;refusal="span past the end of the file";return -1;}
  return fd;
}

/* SHA-256 of prefix + the span: an object identity, computed in place. */
static V hashspan(V pv,V offv,V lenv,V prefixv){
  long long off=num(offv),len=num(lenv);struct stat st;
  int fd=openspan(pv,off,len,&st);if(fd<0)return failed();
  hctx c;if(!h_init(&c)){close(fd);return os_err("SHA-256 unavailable");}
  str_t*pre=(str_t*)prefixv;h_update(&c,pre->bytes,pre->len);
  int err=pump(fd,len,-1,&c);unsigned char d[32];h_final(&c,d);
  if(!err&&len<0&&!same(fd,&st)){refusal="file changed while reading; retry";err=EIO;}
  close(fd);if(err){errno=err;return failed();}
  char hex[64];hexof(d,hex);return os_ok(os_str(hex,64));
}
FPR_FN_CSTACK(fpr_g_Qfs_x2ehash,hashspan,4);

/* Publish prefix + a span as the immutable object `dst`, which it must hash
 * to: the bytes are hashed as they are copied, so a source that changed
 * after it was identified publishes nothing. */
typedef struct { V src; long long off,len; str_t*prefix; str_t*id; } span_t;
static int fill_span(int out,void*ctx){span_t*s=ctx;struct stat st;
  int fd=openspan(s->src,s->off,s->len,&st);if(fd<0)return errno?errno:EIO;
  hctx c;if(!h_init(&c)){close(fd);return EIO;}
  h_update(&c,s->prefix->bytes,s->prefix->len);
  int err=writeall(out,s->prefix->bytes,s->prefix->len);
  if(!err)err=pump(fd,s->len,out,&c);
  unsigned char d[32];h_final(&c,d);char hex[64];hexof(d,hex);
  if(!err&&s->len<0&&!same(fd,&st)){refusal="file changed while reading; retry";err=EIO;}
  if(!err&&(s->id->len!=64||memcmp(hex,s->id->bytes,64))){refusal="content changed since it was identified; retry";err=EIO;}
  close(fd);return err;
}
static V storespan(V srcv,V offv,V lenv,V prefixv,V dstv,V idv){
  span_t s={srcv,num(offv),num(lenv),(str_t*)prefixv,(str_t*)idv};
  return publish(dstv,1,fill_span,&s);
}
FPR_FN_CSTACK(fpr_g_Qfs_x2estore,storespan,6);

/* Replace working file `dst` with object `obj` less its first `skip` bytes
 * (its kind line), checking the WHOLE object against `id` on the way. */
typedef struct { V obj; long long skip; str_t*id; } extract_t;
static int fill_extract(int out,void*ctx){extract_t*x=ctx;struct stat st;
  int fd=openreg(x->obj,&st);if(fd<0)return errno?errno:EIO;
  hctx c;if(!h_init(&c)){close(fd);return EIO;}
  int err=pump(fd,x->skip,-1,&c);if(!err)err=pump(fd,-1,out,&c);
  unsigned char d[32];h_final(&c,d);char hex[64];hexof(d,hex);
  if(!err&&(x->id->len!=64||memcmp(hex,x->id->bytes,64))){refusal="object integrity failure";err=EIO;}
  close(fd);return err;
}
static V extract(V objv,V dstv,V skipv,V idv){
  extract_t x={objv,num(skipv),(str_t*)idv};
  return publish(dstv,0,fill_extract,&x);
}
FPR_FN_CSTACK(fpr_g_Qfs_x2eextract,extract,4);

/* A file from `off` to its end, to standard output. */
static V emit(V pv,V offv){struct stat st;int fd=openspan(pv,num(offv),-1,&st);if(fd<0)return failed();
  int err=pump(fd,-1,STDOUT_FILENO,NULL);close(fd);if(err){errno=err;return failed();}return os_ok((V)&fpr_unit);}
FPR_FN_CSTACK(fpr_g_Qfs_x2eemit,emit,2);

static V sizeof_(V pv){struct stat st;int fd=openreg(pv,&st);if(fd<0)return failed();close(fd);return os_ok(TAG((sw)st.st_size));}
FPR_FN_CSTACK(fpr_g_Qfs_x2esize,sizeof_,1);

/* exactly `len` bytes at `off`: a frame header, never a body */
static V readat(V pv,V offv,V lenv){long long off=num(offv),len=num(lenv);struct stat st;
  if(len<0){errno=EINVAL;return os_errno();}
  int fd=openspan(pv,off,len,&st);if(fd<0)return failed();
  char*b=malloc(len?len:1);if(!b){close(fd);return os_err("out of memory");}
  long long at=0;int err=0;
  while(at<len){ssize_t n=read(fd,b+at,len-at);if(n<0&&errno==EINTR)continue;if(n<=0){err=n<0?errno:EIO;break;}at+=n;}
  close(fd);if(err){free(b);errno=err;return os_errno();}
  V out=os_ok(os_str(b,len));free(b);return out;}
FPR_FN_CSTACK(fpr_g_Qfs_x2ereadAt,readat,3);

/* Scratch files (a transfer being assembled or received): appended, never
 * synchronized, removed when the transfer ends. */
static int openappend(V pv){char*name=NULL;int dir=parent(pv,&name);if(dir<0)return -1;
  int fd=openat(dir,name,O_WRONLY|O_APPEND|O_CREAT|O_NOFOLLOW|O_CLOEXEC,0600);int e=errno;free(name);close(dir);errno=e;return fd;}
static V appendb(V pv,V bv){int fd=openappend(pv);if(fd<0)return failed();str_t*b=(str_t*)bv;
  int err=writeall(fd,b->bytes,b->len);if(close(fd)<0&&!err)err=errno;if(err){errno=err;return os_errno();}return os_ok((V)&fpr_unit);}
FPR_FN_CSTACK(fpr_g_Qfs_x2eappend,appendb,2);
/* a frame: the file's length as four big-endian bytes, then the file */
static V appendframe(V dstv,V srcv){struct stat st;int in=openreg(srcv,&st);if(in<0)return failed();
  if(st.st_size>0xffffffffLL){close(in);return os_err("an object of 4 GiB or more does not fit a frame");}
  int out=openappend(dstv);if(out<0){V e=failed();close(in);return e;}
  unsigned char h[4]={(unsigned char)(st.st_size>>24),(unsigned char)(st.st_size>>16),(unsigned char)(st.st_size>>8),(unsigned char)st.st_size};
  int err=writeall(out,h,4);if(!err)err=pump(in,st.st_size,out,NULL);
  if(!err&&!same(in,&st)){refusal="file changed while reading; retry";err=EIO;}
  close(in);if(close(out)<0&&!err)err=errno;if(err){errno=err;return failed();}return os_ok((V)&fpr_unit);}
FPR_FN_CSTACK(fpr_g_Qfs_x2eappendFrame,appendframe,2);

/* n bytes from the host's entropy source, as hex: a bearer token */
static V randomhex(V nv){long long n=num(nv);if(n<1||n>4096)return os_err("random: 1 to 4096 bytes");
  unsigned char*b=malloc(n);char*hex=malloc(2*n);if(!b||!hex){free(b);free(hex);return os_err("out of memory");}
  for(long long at=0;at<n;at+=256){size_t k=(n-at)>256?256:(size_t)(n-at);if(getentropy(b+at,k)<0){free(b);free(hex);return os_errno();}}
  const char*digits="0123456789abcdef";for(long long i=0;i<n;i++){hex[2*i]=digits[b[i]>>4];hex[2*i+1]=digits[b[i]&15];}
  V out=os_ok(os_str(hex,2*n));free(b);free(hex);return out;}
FPR_FN_CSTACK(fpr_g_Qfs_x2erandom,randomhex,1);

/* Remove a regular working file or completed checkout journal durably. */
static V removefile(V pv){char *name=NULL;int fd=parent(pv,&name);if(fd<0)return failed();
  struct stat st;int r=fstatat(fd,name,&st,AT_SYMLINK_NOFOLLOW);
  if(r==0&&!S_ISREG(st.st_mode)){free(name);close(fd);return os_err("not a regular file");}
  if(r==0)r=unlinkat(fd,name,0);if(r==0)r=fsync(fd);V out=os_unit_or_errno(r);free(name);close(fd);return out;
}
FPR_FN_CSTACK(fpr_g_Qfs_x2eremove,removefile,1);

/* Remove an EMPTY directory (a checkout that deleted its last file, or that
 * puts a file where a directory stood).  A directory that holds anything is
 * refused by the host, which is the point. */
static V removedir(V pv){char *name=NULL;int fd=parent(pv,&name);if(fd<0)return failed();
  int r=unlinkat(fd,name,AT_REMOVEDIR);if(r==0)r=fsync(fd);V out=os_unit_or_errno(r);free(name);close(fd);return out;
}
FPR_FN_CSTACK(fpr_g_Qfs_x2ermdir,removedir,1);

/* Release the repository lock, keeping the root open: the listener spools
 * transfers under .qrepo while each request's worker takes the lock. */
static V endrepo(V unit){(void)unit;if(lockfd>=0){close(lockfd);lockfd=-1;}
  signal(SIGPIPE,SIG_IGN); /* a peer that hangs up is not the listener's death */
  return (V)&fpr_unit;}
FPR_FN_CSTACK(fpr_g_Qfs_x2eend,endrepo,1);
