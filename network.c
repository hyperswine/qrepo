/* Loopback experiment transport. Repository/protocol policy stays in qr.fpr.
 * Requests run in fresh native processes: a malformed request cannot terminate
 * the listener, and request allocations are reclaimed on process exit. */
#include "os_value.h"
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <sys/time.h>
#include <unistd.h>
#include <signal.h>
#include <stdio.h>
#ifdef __APPLE__
#include <mach-o/dyld.h>
#endif
#define FRAME_MAX (16u*1024u*1024u)
#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif
static int transfer(int fd,void *bytes,size_t n,int sending){
  unsigned char*p=bytes;
  while(n){ssize_t r=sending?send(fd,p,n,MSG_NOSIGNAL):recv(fd,p,n,0);
    if(r<0&&errno==EINTR)continue;if(r<=0){if(!r)errno=ECONNRESET;return -1;}p+=r;n-=(size_t)r;}
  return 0;
}
static void configure(int fd){struct timeval t={5,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&t,sizeof t);setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&t,sizeof t);
#ifdef SO_NOSIGPIPE
  int yes=1;setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&yes,sizeof yes);
#endif
}
static int sendframe(int fd,V value){str_t*s=(str_t*)value;if(s->len>FRAME_MAX){errno=EMSGSIZE;return -1;}
  uint32_t n=htonl((uint32_t)s->len);return transfer(fd,&n,4,1)||transfer(fd,s->bytes,s->len,1)?-1:0;}
static V receiveframe(int fd){uint32_t n;if(transfer(fd,&n,4,0))return os_errno();n=ntohl(n);
  if(n>FRAME_MAX)return os_err("remote frame exceeds 16 MiB experimental limit");
  char*p=malloc(n?n:1);if(!p)return os_err("out of memory");
  if(transfer(fd,p,n,0)){V e=os_errno();free(p);return e;}V result=os_ok(os_str(p,n));free(p);return result;}
static V exchange(V pv,V request){int port=(int)UNTAG(pv);if(port<1||port>65535)return os_err("invalid port");
  int fd=socket(AF_INET,SOCK_STREAM,0);if(fd<0)return os_errno();configure(fd);
  struct sockaddr_in a={0};a.sin_family=AF_INET;a.sin_port=htons(port);a.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
  if(connect(fd,(struct sockaddr*)&a,sizeof a)<0){V e=os_errno();close(fd);return e;}
  V result=sendframe(fd,request)?os_errno():receiveframe(fd);close(fd);return result;}
FPR_FN_CSTACK(fpr_g_Qnet_x2eexchange,exchange,2);
static V request(V unit){(void)unit;return receiveframe(STDIN_FILENO);}
FPR_FN_CSTACK(fpr_g_Qnet_x2erequest,request,1);
static V reply(V body){return sendframe(STDOUT_FILENO,body)?os_errno():os_ok((V)&fpr_unit);}
FPR_FN_CSTACK(fpr_g_Qnet_x2ereply,reply,1);
static V serve(V rootv,V portv){int port=(int)UNTAG(portv);if(port<1||port>65535)return os_err("invalid port");
  char *root=os_cstr(rootv,"root");if(strlen(root)!=((str_t*)rootv)->len){free(root);return os_err("NUL in root");}
  char *exe=NULL;
#ifdef __APPLE__
  uint32_t size=0;_NSGetExecutablePath(NULL,&size);exe=malloc(size);if(!exe||_NSGetExecutablePath(exe,&size)){free(exe);free(root);return os_err("cannot resolve executable");}
#else
  size_t size=256;for(;;){exe=malloc(size);if(!exe){free(root);return os_err("out of memory");}ssize_t n=readlink("/proc/self/exe",exe,size-1);if(n<0){free(exe);free(root);return os_errno();}if((size_t)n<size-1){exe[n]=0;break;}free(exe);size*=2;}
#endif
  int fd=socket(AF_INET,SOCK_STREAM,0);if(fd<0){free(exe);free(root);return os_errno();}
  int yes=1;setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&yes,sizeof yes);
  struct sockaddr_in a={0};a.sin_family=AF_INET;a.sin_port=htons(port);a.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
  if(bind(fd,(struct sockaddr*)&a,sizeof a)<0||listen(fd,32)<0){V e=os_errno();close(fd);free(exe);free(root);return e;}
  fprintf(stderr,"QRepo listening on 127.0.0.1:%d (local experiment, one request at a time)\n",port);fflush(stderr);
  char *argv[]={exe,"--root",root,"__rpc",NULL};
  for(;;){int conn=accept(fd,NULL,NULL);if(conn<0){if(errno==EINTR)continue;V e=os_errno();close(fd);free(exe);free(root);return e;}configure(conn);
    pid_t pid=fork();
    if(pid==0){/* Only async-signal-safe operations until exec. */
      close(fd);if(dup2(conn,STDIN_FILENO)<0||dup2(conn,STDOUT_FILENO)<0)_exit(126);if(conn>1)close(conn);alarm(20);execv(exe,argv);_exit(127);
    }
    close(conn);if(pid<0){perror("qrepo fork");continue;}
    int status;while(waitpid(pid,&status,0)<0&&errno==EINTR){}
  }
}
FPR_FN_CSTACK(fpr_g_Qnet_x2eserve,serve,2);
