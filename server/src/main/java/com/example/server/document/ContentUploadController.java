package com.example.server.document;

import com.example.server.common.Result;
import com.example.server.service.AuthService;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/documents/uploads")
public class ContentUploadController {
    private final ContentUploadService uploads;
    public ContentUploadController(ContentUploadService uploads){this.uploads=uploads;}
    @PostMapping("/init")
    public Result<ContentUploadService.Session> initialize(@RequestBody ContentUploadService.Request request,
            @RequestAttribute(AuthService.REQUEST_USER_ID) Long user){
        return Result.ok(uploads.initialize(request,user));
    }
}
