package com.example.server.controller;

import com.example.server.common.Result;
import com.example.server.service.AuthService;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/admin/users")
public class AdminUserController {
    private final AuthService authService;
    public AdminUserController(AuthService authService) { this.authService = authService; }

    public record ResetPasswordRequest(String password) {}

    @PostMapping("/{id}/reset-password")
    public Result<Void> resetPassword(@PathVariable Long id,
                                      @RequestBody ResetPasswordRequest request,
                                      @RequestAttribute(AuthService.REQUEST_USER_ID) Long operatorId) {
        authService.resetPassword(operatorId, id, request == null ? null : request.password());
        return Result.ok();
    }
}
