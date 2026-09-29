/* QRepo's host seam: descriptor-relative, no-follow filesystem access.
 * Repository format, hashing and command policy live in FP-RISC. */
#include "os_value.h"
#include <dirent.h>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#include <stdio.h>
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
/* mode 1 creates an immutable object exclusively; mode 0 replaces a ref.
 * Write + fsync temp, publish, then fsync parent. Never truncate the target. */
static V writefile(V pv,V bv,V mv){
  char*name=NULL;int dir=parent(pv,&name);if(dir<0)return failed();
  struct stat st;int sr=fstatat(dir,name,&st,AT_SYMLINK_NOFOLLOW);
  if(sr==0&&!S_ISREG(st.st_mode)){free(name);close(dir);return os_err("not a regular file");}
  if(sr<0&&errno!=ENOENT){V out=os_errno();free(name);close(dir);return out;}
  if(sr==0&&UNTAG(mv)){free(name);close(dir);return os_err("object already exists");}
  char tmp[96];int fd;
  do{snprintf(tmp,sizeof tmp,".qr-tmp-%ld-%lu",(long)getpid(),++serial);fd=openat(dir,tmp,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);}while(fd<0&&errno==EEXIST);
  if(fd<0){V out=os_errno();free(name);close(dir);return out;}
  str_t*b=(str_t*)bv;size_t at=0;int err=0;
  while(at<b->len){ssize_t n=write(fd,b->bytes+at,b->len-at);if(n<0&&errno==EINTR)continue;if(n<=0){err=n<0?errno:EIO;break;}at+=n;}
  if(!err&&sr==0&&fchmod(fd,st.st_mode&0777)<0)err=errno;
  if(!err&&fsync(fd)<0)err=errno;if(close(fd)<0&&!err)err=errno;
  if(!err)fault("before",pv);
  if(!err){int r=UNTAG(mv)?linkat(dir,tmp,dir,name,0):renameat(dir,tmp,dir,name);if(r<0)err=errno;}
  if(!err)fault("after",pv);
  unlinkat(dir,tmp,0);
  if(!err&&fsync(dir)<0)err=errno;
  free(name);close(dir);if(err){errno=err;return os_errno();}return os_ok((V)&fpr_unit);
}
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

static V endrepo(V unit){(void)unit;if(lockfd>=0){close(lockfd);lockfd=-1;}if(rootfd>=0){close(rootfd);rootfd=-1;}return (V)&fpr_unit;}
FPR_FN_CSTACK(fpr_g_Qfs_x2eend,endrepo,1);
