package com.example.server.document.adaptive;
import com.example.server.common.ErrorCode;
import com.example.server.exception.BusinessException;
/** Typed transport/inference failure; never interpreted as an empty hit list. */
public class RetrievalServiceException extends BusinessException {
    private final String failureType;private final int httpStatus;
    public RetrievalServiceException(String type,int status){super(ErrorCode.SERVICE_UNAVAILABLE,"检索阶段失败："+type+"（HTTP "+status+"）");failureType=type;httpStatus=status;}
    public String failureType(){return failureType;}public int httpStatus(){return httpStatus;}
}
